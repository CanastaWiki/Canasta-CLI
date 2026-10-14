"""remove on a stopped Kubernetes instance refuses before changing anything,
and a K8s pod lookup that finds no Running pod fails with a plain message.

The fake kubectl mimics the real one: a jsonpath of `{.items[0]...}` over
an empty pod list exits 1 with an index error, while `{.items[*]...}`
prints nothing.
"""

import os
import stat
import subprocess
import sys

import yaml

from _orchestrator import INSTANCE_IS_K8S_VARS

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
GET_POD = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks",
                       "k8s_get_pod.yml")
REMOVE = os.path.join(REPO_ROOT, "playbooks", "remove.yml")
COMMON = os.path.join(REPO_ROOT, "roles", "common")

FAKE_KUBECTL = """#!/bin/sh
echo "$*" >> "$KUBECTL_LOG"
case "$*" in
  *"items[0]"*)
    if [ -z "$FAKE_PODS" ]; then
      echo 'error: error executing jsonpath "{.items[0].metadata.name}": array index out of bounds' >&2
      exit 1
    fi
    echo "${FAKE_PODS%% *}" ;;
  *) printf '%s' "$FAKE_PODS" ;;
esac
"""

RAW_ERROR = "array index out of bounds"


def _run(tmp_path, tasks, play_vars, pods):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    kubectl = bindir / "kubectl"
    kubectl.write_text(FAKE_KUBECTL)
    kubectl.chmod(kubectl.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "kubectl.log"
    log.write_text("")
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": dict(INSTANCE_IS_K8S_VARS, canasta_root=REPO_ROOT,
                     ansible_python_interpreter=sys.executable, **play_vars),
        "tasks": tasks,
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               PATH="%s:%s" % (bindir, os.environ.get("PATH", "")),
               FAKE_PODS=pods, KUBECTL_LOG=str(log),
               ANSIBLE_LIBRARY=os.path.join(COMMON, "library"),
               ANSIBLE_MODULE_UTILS=os.path.join(COMMON, "module_utils"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env,
                          cwd=tmp_path)
    return proc, proc.stdout + proc.stderr, log.read_text()


class TestGetPod:
    TASKS = [
        {"ansible.builtin.include_tasks": GET_POD},
        {"ansible.builtin.debug": {"msg": "POD=[{{ _k8s_pod_name }}]"}},
    ]
    VARS = {"_k8s_namespace": "canasta-site", "_k8s_component": "db"}

    def test_no_running_pod_gives_a_plain_message(self, tmp_path):
        proc, out, _ = _run(tmp_path, self.TASKS, self.VARS, "")
        assert proc.returncode != 0
        assert "No running pod found for component 'db'" in out
        assert RAW_ERROR not in out

    def test_first_running_pod_is_chosen(self, tmp_path):
        proc, out, _ = _run(tmp_path, self.TASKS, self.VARS, "db-0 db-1")
        assert proc.returncode == 0, out
        assert "POD=[db-0]" in out


def _remove_precheck_tasks():
    with open(REMOVE) as f:
        tasks = yaml.safe_load(f)
    names = [t.get("name") for t in tasks]
    start = names.index("Choose the databases to drop")
    end = names.index("Require a running database pod on Kubernetes")
    return tasks[start:end + 1] + [
        {"ansible.builtin.debug": {"msg": "PRECHECK PASSED"}}]


def _remove_vars(tmp_path, orchestrator):
    inst = tmp_path / "inst"
    inst.mkdir(exist_ok=True)
    (inst / ".env").write_text("MYSQL_PASSWORD=x\n")
    return {
        "instance_path": str(inst),
        "instance_id": "site",
        "instance_orchestrator": orchestrator,
        "compose_command": "docker compose",
        "wiki": "docs",
        "_remove_wikis": {
            "wiki_ids": ["main", "docs"],
            "db_groups": [{"wiki": "main", "databases": ["main"]},
                          {"wiki": "docs", "databases": ["docs"]}],
        },
    }


class TestRemoveStoppedK8s:
    def test_refuses_with_a_plain_message_when_db_is_down(self, tmp_path):
        proc, out, log = _run(tmp_path, _remove_precheck_tasks(),
                              _remove_vars(tmp_path, "kubernetes"), "")
        assert proc.returncode != 0
        assert "Cannot remove wiki 'docs'" in out
        assert "no running db pod to drop its database in" in out
        assert "canasta start -i site" in out
        assert RAW_ERROR not in out
        assert "PRECHECK PASSED" not in out
        assert "component=db" in log

    def test_proceeds_when_db_pod_is_running(self, tmp_path):
        proc, out, _ = _run(tmp_path, _remove_precheck_tasks(),
                            _remove_vars(tmp_path, "kubernetes"), "db-0")
        assert proc.returncode == 0, out
        assert "PRECHECK PASSED" in out

    def test_compose_does_not_look_for_a_pod(self, tmp_path):
        proc, out, log = _run(tmp_path, _remove_precheck_tasks(),
                              _remove_vars(tmp_path, "compose"), "")
        assert proc.returncode == 0, out
        assert log == ""

    def test_check_runs_before_anything_changes(self):
        with open(REMOVE) as f:
            tasks = yaml.safe_load(f)
        names = [t.get("name") for t in tasks]
        check = names.index("Require a running database pod on Kubernetes")
        assert check < names.index("Remove wiki images inside container")
        assert check < names.index("Remove wiki from wikis.yaml")
