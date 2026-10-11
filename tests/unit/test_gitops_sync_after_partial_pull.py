"""The .env and wikis.yaml syncs wait for a pulled commit to be applied.

A pull that moved HEAD and then stopped before rendering leaves the
index holding the new sources while config/wikis.yaml and .env still
hold the old values. Comparing them then would capture the old values
into the sources, undoing the pulled change.
"""

import os
import subprocess
import sys

import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TASKS = os.path.join(REPO_ROOT, "roles", "gitops", "tasks")
SYNC_WIKIS = os.path.join(TASKS, "sync_wikis_yaml.yml")
SYNC_ENV = os.path.join(TASKS, "sync_env.yml")
COMMON = os.path.join(REPO_ROOT, "roles", "common")
PENDING = "_gitops_apply_pending.yml"

MAIN = "- id: main\n  url: {{wiki_url_main}}\n  name: Main\n"
DOCS = "- id: docs\n  url: {{wiki_url_docs}}\n  name: Docs\n"
LIVE = "wikis:\n- id: main\n  url: localhost/main\n  name: Main\n"


def _git(inst, *args):
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=inst, check=True, capture_output=True, text=True).stdout.strip()


def _repo(tmp_path, live, applied_first):
    inst = tmp_path / "inst"
    (inst / "config").mkdir(parents=True)
    _git(inst, "init", "-q")
    (inst / "wikis.yaml.template").write_text("wikis:\n" + MAIN)
    _git(inst, "add", "wikis.yaml.template")
    _git(inst, "commit", "-qm", "one")
    first = _git(inst, "rev-parse", "HEAD")
    (inst / "wikis.yaml.template").write_text("wikis:\n" + MAIN + DOCS)
    _git(inst, "commit", "-qam", "two")
    head = _git(inst, "rev-parse", "HEAD")
    (inst / ".gitops-applied").write_text(
        (first if applied_first else head) + "\n")
    (inst / "config" / "wikis.yaml").write_text(live)
    return inst


def _run_sync(tmp_path, inst):
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {"instance_path": str(inst), "canasta_root": REPO_ROOT,
                 "ansible_python_interpreter": sys.executable},
        "tasks": [{"ansible.builtin.include_tasks": SYNC_WIKIS}],
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_FILTER_PLUGINS=os.path.join(REPO_ROOT, "filter_plugins"),
               ANSIBLE_LIBRARY=os.path.join(COMMON, "library"),
               ANSIBLE_MODULE_UTILS=os.path.join(COMMON, "module_utils"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    return subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env,
                          cwd=tmp_path)


class TestWikisSync:
    def test_unapplied_pull_is_not_captured_back(self, tmp_path):
        inst = _repo(tmp_path, LIVE, applied_first=True)
        proc = _run_sync(tmp_path, inst)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "id: docs" in (inst / "wikis.yaml.template").read_text()
        assert _git(inst, "status", "--porcelain",
                    "--untracked-files=no") == ""

    def test_applied_checkout_still_captures_a_live_edit(self, tmp_path):
        inst = _repo(tmp_path, LIVE, applied_first=False)
        proc = _run_sync(tmp_path, inst)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "id: docs" not in (inst / "wikis.yaml.template").read_text()


class TestWiring:
    def _includes_pending(self, path):
        with open(path) as f:
            return any(PENDING in str(t.get("ansible.builtin.include_tasks"))
                       for t in yaml.safe_load(f))

    def test_both_syncs_check_for_a_pending_apply(self):
        assert self._includes_pending(SYNC_WIKIS)
        assert self._includes_pending(SYNC_ENV)
