"""remove on a stopped instance refuses before changing anything.

The fake docker prints FAKE_OUT for `ps` and logs everything else.
"""

import os
import stat
import subprocess
import sys

import yaml


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
REMOVE = os.path.join(REPO_ROOT, "playbooks", "remove.yml")
COMMON = os.path.join(REPO_ROOT, "roles", "common")

FAKE_DOCKER = """#!/bin/sh
echo "docker $*" >> "$CMD_LOG"
case "$1" in
  ps) printf '%s' "$FAKE_OUT" ;;
esac
"""


def _run(tmp_path, tasks, play_vars, fake_out):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    exe = bindir / "docker"
    exe.write_text(FAKE_DOCKER)
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "cmd.log"
    log.write_text("")
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": dict(canasta_root=REPO_ROOT,
                     ansible_python_interpreter=sys.executable, **play_vars),
        "tasks": tasks,
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               PATH="%s:%s" % (bindir, os.environ.get("PATH", "")),
               FAKE_OUT=fake_out, CMD_LOG=str(log),
               ANSIBLE_ROLES_PATH=os.path.join(REPO_ROOT, "roles"),
               ANSIBLE_FILTER_PLUGINS=os.path.join(REPO_ROOT, "filter_plugins"),
               ANSIBLE_LIBRARY=os.path.join(COMMON, "library"),
               ANSIBLE_MODULE_UTILS=os.path.join(COMMON, "module_utils"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env,
                          cwd=tmp_path)
    return proc, proc.stdout + proc.stderr, log.read_text()


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


def _remove_vars(tmp_path, env_text="MYSQL_PASSWORD=x\n"):
    inst = tmp_path / "inst"
    (inst / "config").mkdir(parents=True, exist_ok=True)
    (inst / ".env").write_text(env_text)
    (inst / "config" / "wikis.yaml").write_text(WIKIS_YAML)
    return {
        "instance_path": str(inst),
        "instance_id": "site",
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
        assert "REMOVE CONTINUED" not in out
        assert " exec " not in log
        assert (tmp_path / "inst" / "config" / "wikis.yaml").read_text() \
            == WIKIS_YAML

    def test_stopped_instance_is_refused(self, tmp_path):
        proc, out, log = _run(tmp_path, _remove_tasks(),
                              _remove_vars(tmp_path), "")
        self._assert_refused_unchanged(tmp_path, proc, out, log)

    def test_external_db_needs_the_web_service(self, tmp_path):
        play_vars = _remove_vars(tmp_path,
                                 "USE_EXTERNAL_DB=true\nMYSQL_HOST=x\n")
        proc, out, log = _run(tmp_path, _remove_tasks(), play_vars, "db\n")
        self._assert_refused_unchanged(tmp_path, proc, out, log)

    def test_running_instance_proceeds(self, tmp_path):
        proc, out, log = _run(tmp_path, _remove_tasks(),
                              _remove_vars(tmp_path), "web\ndb\n")
        assert proc.returncode == 0, out
        assert "REMOVE CONTINUED" in out
        assert "id: docs" not in (
            tmp_path / "inst" / "config" / "wikis.yaml").read_text()
