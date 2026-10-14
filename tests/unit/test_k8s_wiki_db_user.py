"""Which database account a Kubernetes instance's MediaWiki uses, per host.

The account is this host's choice: it lives in this host's database server.
A direct deploy takes it from .env through a values-db-account.yaml overlay
applied last, so the values.yaml a gitops repo shares cannot carry it to
another host. A gitops render takes it from this host's vars
(wiki_db_user), and a host whose vars lack the key renders root.
"""

import json
import os
import subprocess
import sys

import pytest
import yaml

from _orchestrator import INSTANCE_IS_K8S_VARS

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
TASKS = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks")
GITOPS = os.path.join(REPO_ROOT, "roles", "gitops", "tasks")
COMMON = os.path.join(REPO_ROOT, "roles", "common")
USER_TASK = os.path.join(TASKS, "k8s_wiki_db_user.yml")
HELM_DEPLOY = os.path.join(TASKS, "helm_deploy.yml")
RENDER = os.path.join(GITOPS, "render_kubernetes.yml")


def play(tmp_path, play_vars, tasks):
    playfile = tmp_path / "play.yml"
    playfile.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": dict(INSTANCE_IS_K8S_VARS, canasta_root=REPO_ROOT,
                     ansible_python_interpreter=sys.executable,
                     instance_orchestrator="kubernetes", **play_vars),
        "tasks": tasks,
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_FILTER_PLUGINS=os.path.join(REPO_ROOT, "filter_plugins"),
               ANSIBLE_LIBRARY=os.path.join(COMMON, "library"),
               ANSIBLE_MODULE_UTILS=os.path.join(COMMON, "module_utils"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    return subprocess.run([playbook, "-i", "localhost,", str(playfile)],
                          capture_output=True, text=True, env=env,
                          cwd=tmp_path)


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f)


class TestAccountFromEnv:
    def _run(self, tmp_path, env_text):
        inst = tmp_path / "inst"
        inst.mkdir()
        (inst / ".env").write_text(env_text)
        out = tmp_path / "out.json"
        proc = play(tmp_path, {"instance_path": str(inst)}, [
            {"ansible.builtin.include_tasks": USER_TASK},
            {"ansible.builtin.copy": {
                "content": "{{ {'user': _k8s_wiki_db_user} | to_json }}",
                "dest": str(out)}},
        ])
        assert proc.returncode == 0, proc.stdout + proc.stderr
        return json.loads(out.read_text())["user"], (inst / ".env").read_text()

    @pytest.mark.parametrize("env_text, expected", [
        ("MYSQL_PASSWORD=x\n", "root"),
        ("WIKI_DB_USER=root\n", "root"),
        ("WIKI_DB_USER=mediawiki\n", "mediawiki"),
        ("WIKI_DB_USER=mediawiki\nUSE_EXTERNAL_DB=true\n", "root"),
    ])
    def test_account(self, tmp_path, env_text, expected):
        user, after = self._run(tmp_path, env_text)
        assert user == expected
        assert after == env_text

    def test_opt_out_puts_a_moved_instance_back_on_root(self, tmp_path):
        user, after = self._run(
            tmp_path, "WIKI_DB_USER=mediawiki\nCANASTA_DB_ROOT_ACCOUNT=true\n")
        assert user == "root"
        assert "WIKI_DB_USER=root" in after.splitlines()


class TestDirectDeploy:
    def test_overlay_is_written_and_applied_last(self):
        tasks = _load(HELM_DEPLOY)
        names = [t.get("name") for t in tasks]
        write = tasks[names.index("Write MediaWiki's database account override")]
        assert write["ansible.builtin.copy"]["dest"].endswith(
            "/values-db-account.yaml")
        assert "_k8s_wiki_db_user" in write["ansible.builtin.copy"]["content"]
        assert names.index("Choose MediaWiki's database account") < \
            names.index("Write MediaWiki's database account override")
        deploy = next(t for t in tasks if t.get("name") == "Deploy Canasta Helm release")
        cmd = " ".join(deploy["block"][0]["vars"]["rx_cmd"].split())
        last_f = cmd.rindex("-f ")
        assert cmd[last_f:].startswith("-f {{ instance_path }}/values-db-account.yaml")

    def test_overlay_is_not_committed(self):
        with open(os.path.join(REPO_ROOT, "roles", "gitops", "files",
                               "gitignore.kubernetes")) as f:
            assert "values-db-account.yaml" in f.read().splitlines()


class TestGitopsRender:
    TEMPLATE = {
        "instance": {"id": "inst"},
        "domains": ["example.com"],
        "db": {"enabled": True, "wikiUser": "mediawiki", "image": "mariadb:11.4"},
        "configData": {"web": {}},
    }

    def _render(self, tmp_path, host_vars):
        inst = tmp_path / "inst"
        (inst / "hosts" / "h1").mkdir(parents=True)
        (inst / ".gitops-host").write_text("h1\n")
        (inst / "hosts" / "h1" / "vars.yaml").write_text(yaml.safe_dump(host_vars))
        (inst / "hosts" / "_shared").mkdir()
        (inst / "hosts" / "_shared" / "vars.yaml").write_text(
            yaml.safe_dump({"wiki_db_user": "shared"}))
        (inst / "values.template.yaml").write_text(yaml.safe_dump(self.TEMPLATE))
        proc = play(tmp_path, {"instance_path": str(inst), "instance_id": "inst"},
                    [{"ansible.builtin.include_tasks": RENDER}])
        assert proc.returncode == 0, proc.stdout + proc.stderr
        return yaml.safe_load(
            (inst / "hosts" / "h1" / "rendered-values.yaml").read_text())

    def test_host_vars_choose_the_account(self, tmp_path):
        rendered = self._render(tmp_path, {"instance_id": "inst",
                                           "wiki_db_user": "mediawiki"})
        assert rendered["db"]["wikiUser"] == "mediawiki"
        assert rendered["db"]["image"] == "mariadb:11.4"

    def test_a_host_without_the_key_renders_root(self, tmp_path):
        # The shared template and shared vars both say otherwise.
        rendered = self._render(tmp_path, {"instance_id": "inst"})
        assert rendered["db"]["wikiUser"] == "root"

    def test_init_and_join_write_the_host_var(self):
        for name in ("init_kubernetes.yml", "join_kubernetes.yml"):
            tasks = _load(os.path.join(GITOPS, name))
            names = [t.get("name") for t in tasks]
            read = names.index("Read MediaWiki's database account")
            assert tasks[read]["ansible.builtin.include_tasks"].endswith(
                "/roles/orchestrator/tasks/k8s_wiki_db_user.yml")
            write = next(i for i, n in enumerate(names)
                         if n in ("Extract per-host vars from values",
                                  "Write host vars from current values"))
            assert read < write
            assert 'wiki_db_user: "{{ _k8s_wiki_db_user }}"' in \
                tasks[write]["ansible.builtin.copy"]["content"], name

    def test_init_renders_the_account_for_argo(self):
        tasks = _load(os.path.join(GITOPS, "init_kubernetes.yml"))
        write = next(t for t in tasks if t.get("name")
                     == "Write rendered-values.yaml (initial render = current values)")
        assert "'wikiUser': _k8s_wiki_db_user" in write["ansible.builtin.copy"]["content"]
