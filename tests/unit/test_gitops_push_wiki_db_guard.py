"""gitops push refuses values whose database account has no password.

Values that select MediaWiki's own account make the web pods read
WIKI_DB_PASSWORD from the db Secret. If the live Secret lacks the key (it
was never added, or something removed it before the push), Argo CD would
roll out pods that cannot start, so the push stops before committing.
These run the guard's tasks with the Secret read replaced by a fixture.
"""

import copy
import os
import subprocess
import sys

import pytest
import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
PUSH = os.path.join(REPO_ROOT, "roles", "gitops", "tasks", "push_kubernetes.yml")
GUARD = ("Find the database account the pushed values select",
         "Decide whether the pushed values need the account's password",
         "Read the db Secret",
         "Refuse values whose account has no password in the db Secret")


def _tasks():
    with open(PUSH) as f:
        return yaml.safe_load(f)


def _guard(secret_data):
    tasks = {t.get("name"): t for t in _tasks()}
    out = []
    for name in GUARD:
        task = copy.deepcopy(tasks[name])
        if "kubernetes.core.k8s_info" in task:
            info = task.pop("kubernetes.core.k8s_info")
            task.pop("register")
            task.pop("no_log")
            resources = [] if secret_data is None else [{"data": secret_data}]
            task["ansible.builtin.set_fact"] = {
                "_push_db_secret": {"resources": resources},
                "_test_read": info}
        out.append(task)
    return out


def _run(tmp_path, values, secret_data, template=True):
    play_vars = {"instance_id": "inst", "_k8s_namespace": "canasta-inst",
                 "_push_template_check": {"stat": {"exists": template}},
                 "ansible_python_interpreter": sys.executable}
    play_vars["_rendered_values" if template else "_push_merged_values"] = values
    playfile = tmp_path / "play.yml"
    playfile.write_text(yaml.safe_dump([{
        "hosts": "localhost", "connection": "local", "gather_facts": False,
        "vars": play_vars,
        "tasks": _guard(secret_data) + [
            {"ansible.builtin.debug": {"msg": "READ={{ _test_read | default('none') }}"}},
            {"ansible.builtin.debug": {"msg": "GUARD PASSED"}}],
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0")
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    return subprocess.run([playbook, "-i", "localhost,", str(playfile)],
                          capture_output=True, text=True, env=env, cwd=tmp_path)


OWN = {"db": {"enabled": True, "wikiUser": "mediawiki"},
       "secrets": {"dbSecretName": "inst-db-credentials"}}


class TestPushGuard:
    def test_own_account_without_the_key_is_refused(self, tmp_path):
        proc = _run(tmp_path, OWN, {"MYSQL_PASSWORD": "eA=="})
        assert proc.returncode != 0
        assert "Not pushing" in proc.stdout + proc.stderr
        assert "inst-db-credentials Secret has no WIKI_DB_PASSWORD key" in \
            " ".join((proc.stdout + proc.stderr).split())
        assert "GUARD PASSED" not in proc.stdout

    def test_missing_secret_is_refused(self, tmp_path):
        proc = _run(tmp_path, OWN, None)
        assert proc.returncode != 0
        assert "Not pushing" in proc.stdout + proc.stderr

    def test_own_account_with_the_key_is_pushed(self, tmp_path):
        proc = _run(tmp_path, OWN, {"MYSQL_PASSWORD": "eA==",
                                    "WIKI_DB_PASSWORD": "eQ=="})
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "GUARD PASSED" in proc.stdout
        assert "'name': 'inst-db-credentials'" in proc.stdout
        assert "eQ==" not in proc.stdout

    @pytest.mark.parametrize("values", [
        {"db": {"enabled": True, "wikiUser": "root"}},
        {"db": {"enabled": True}},
        {},
        {"db": {"enabled": False, "wikiUser": "mediawiki"}},
    ])
    def test_root_or_external_never_reads_the_secret(self, tmp_path, values):
        proc = _run(tmp_path, values, None)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "GUARD PASSED" in proc.stdout
        assert "READ=none" in proc.stdout

    def test_single_env_repo_checks_its_merged_values(self, tmp_path):
        proc = _run(tmp_path, OWN, {}, template=False)
        assert proc.returncode != 0
        assert "Not pushing" in proc.stdout + proc.stderr

    def test_guard_runs_after_render_and_before_anything_is_staged(self):
        names = [t.get("name") for t in _tasks()]
        assert names.index("Render per-host values") \
            < names.index(GUARD[0]) \
            < names.index(GUARD[-1]) \
            < names.index("Stage changes to the gitops-managed paths")
        read = next(t for t in _tasks() if t.get("name") == "Read the db Secret")
        assert read["no_log"] is True
