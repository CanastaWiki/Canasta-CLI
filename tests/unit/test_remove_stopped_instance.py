"""remove on a stopped instance refuses before changing anything, and K8s
pod lookups that find no Running pod fail with a plain message.

The fake kubectl mimics the real one: a jsonpath that indexes `items[0]` of
an empty pod list exits 1 with an index error, while `items[*]` prints
nothing. The fake docker prints FAKE_OUT for `ps` and logs everything else.
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
CROWDSEC_PREFLIGHT = os.path.join(REPO_ROOT, "roles", "crowdsec", "tasks",
                                  "_preflight.yml")
COMMON = os.path.join(REPO_ROOT, "roles", "common")

FAKE_KUBECTL = """#!/bin/sh
echo "kubectl $*" >> "$CMD_LOG"
case "$*" in
  *"items[0]"*)
    if [ -z "$FAKE_OUT" ]; then
      echo 'error: error executing jsonpath "{.items[0]}": array index out of bounds' >&2
      exit 1
    fi
    echo "${FAKE_OUT%% *}" ;;
  *) printf '%s' "$FAKE_OUT" ;;
esac
"""

FAKE_DOCKER = """#!/bin/sh
echo "docker $*" >> "$CMD_LOG"
case "$1" in
  ps) printf '%s' "$FAKE_OUT" ;;
esac
"""

RAW_ERROR = "array index out of bounds"


def _run(tmp_path, tasks, play_vars, fake_out):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    for name, body in (("kubectl", FAKE_KUBECTL), ("docker", FAKE_DOCKER)):
        exe = bindir / name
        exe.write_text(body)
        exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "cmd.log"
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
               FAKE_OUT=fake_out, CMD_LOG=str(log),
               ANSIBLE_ROLES_PATH=os.path.join(REPO_ROOT, "roles"),
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
        proc, out, _ = _run(tmp_path, self.TASKS, self.VARS, "db-0 \ndb-1 \n")
        assert proc.returncode == 0, out
        assert "POD=[db-0]" in out


WIKIS_YAML = """wikis:
- id: main
  url: localhost/main
  name: Main
- id: docs
  url: localhost/docs
  name: Docs
"""


def _remove_tasks():
    """remove.yml from choosing the databases through the first change to
    wikis.yaml, then a marker."""
    with open(REMOVE) as f:
        tasks = yaml.safe_load(f)
    names = [t.get("name") for t in tasks]
    start = names.index("Choose the databases to drop")
    end = names.index("Remove wiki from wikis.yaml")
    return tasks[start:end + 1] + [
        {"ansible.builtin.debug": {"msg": "REMOVE CONTINUED"}}]


def _remove_vars(tmp_path, orchestrator, env_text="MYSQL_PASSWORD=x\n"):
    inst = tmp_path / "inst"
    (inst / "config").mkdir(parents=True, exist_ok=True)
    (inst / ".env").write_text(env_text)
    (inst / "config" / "wikis.yaml").write_text(WIKIS_YAML)
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


REFUSAL = ("Cannot remove wiki 'docs': instance 'site' is not running, so "
           "its database can't be dropped. Start the instance with "
           "'canasta start -i site', then run remove again.")


class TestRemoveStoppedInstance:
    def _assert_refused_unchanged(self, tmp_path, proc, out, log):
        assert proc.returncode != 0
        assert REFUSAL in " ".join(out.split())
        assert RAW_ERROR not in out
        assert "REMOVE CONTINUED" not in out
        assert " exec " not in log
        assert (tmp_path / "inst" / "config" / "wikis.yaml").read_text() \
            == WIKIS_YAML

    def test_stopped_compose_instance_is_refused(self, tmp_path):
        proc, out, log = _run(tmp_path, _remove_tasks(),
                              _remove_vars(tmp_path, "compose"), "")
        self._assert_refused_unchanged(tmp_path, proc, out, log)

    def test_stopped_k8s_instance_is_refused(self, tmp_path):
        proc, out, log = _run(tmp_path, _remove_tasks(),
                              _remove_vars(tmp_path, "kubernetes"), "")
        self._assert_refused_unchanged(tmp_path, proc, out, log)

    def test_external_db_needs_the_web_service(self, tmp_path):
        play_vars = _remove_vars(tmp_path, "compose",
                                 "USE_EXTERNAL_DB=true\nMYSQL_HOST=x\n")
        proc, out, log = _run(tmp_path, _remove_tasks(), play_vars, "db\n")
        self._assert_refused_unchanged(tmp_path, proc, out, log)

    def test_running_compose_instance_proceeds(self, tmp_path):
        proc, out, log = _run(tmp_path, _remove_tasks(),
                              _remove_vars(tmp_path, "compose"), "web\ndb\n")
        assert proc.returncode == 0, out
        assert "REMOVE CONTINUED" in out
        assert "id: docs" not in (
            tmp_path / "inst" / "config" / "wikis.yaml").read_text()

    def test_running_k8s_instance_proceeds(self, tmp_path):
        tasks = _remove_tasks()
        names = [t.get("name") for t in tasks]
        tasks = tasks[:names.index(
            "Require the instance to be running to drop the wiki's databases") + 1]
        tasks.append({"ansible.builtin.debug": {"msg": "REMOVE CONTINUED"}})
        proc, out, _ = _run(tmp_path, tasks,
                            _remove_vars(tmp_path, "kubernetes"), "web\ndb\n")
        assert proc.returncode == 0, out
        assert "REMOVE CONTINUED" in out


def _crowdsec_k8s_tasks():
    with open(CROWDSEC_PREFLIGHT) as f:
        tasks = yaml.safe_load(f)
    names = [t.get("name") for t in tasks]
    start = names.index("Read CrowdSec enablement from .env")
    end = names.index("Verify the CrowdSec local API is running (Kubernetes)")
    return tasks[start:end + 1] + [
        {"ansible.builtin.debug": {"msg": "PREFLIGHT PASSED"}}]


class TestCrowdsecPreflightK8s:
    def _run(self, tmp_path, fake_out):
        inst = tmp_path / "inst"
        inst.mkdir(exist_ok=True)
        (inst / ".env").write_text("CANASTA_ENABLE_CROWDSEC=true\n")
        return _run(tmp_path, _crowdsec_k8s_tasks(), {
            "instance_path": str(inst), "instance_id": "site",
            "instance_orchestrator": "kubernetes"}, fake_out)

    def test_no_crowdsec_pod_gives_the_plain_message(self, tmp_path):
        proc, out, _ = self._run(tmp_path, "")
        assert proc.returncode != 0
        assert "local API pod is not running" in out
        assert RAW_ERROR not in out

    def test_ready_local_api_passes(self, tmp_path):
        proc, out, _ = self._run(tmp_path, "crowdsec=true |")
        assert proc.returncode == 0, out
        assert "PREFLIGHT PASSED" in out

    def test_only_the_first_crowdsec_pod_counts(self, tmp_path):
        proc, out, _ = self._run(
            tmp_path, "crowdsec=false |crowdsec=true |")
        assert proc.returncode != 0
        assert "PREFLIGHT PASSED" not in out
