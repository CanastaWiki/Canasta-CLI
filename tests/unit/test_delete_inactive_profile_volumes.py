"""delete removes the volumes of a profile that is no longer active.

`compose down -v` only acts on the active profiles, so after CrowdSec was
enabled and later disabled its volumes outlive `down` and delete failed
with "another container is probably still using them". destroy.yml now
removes the project's remaining containers and volumes by their compose
labels before it checks for leftovers.

A delete that still fails must not have done anything irreversible first,
so the backup schedule is removed only after the teardown.
"""

import os
import stat
import subprocess
import sys

import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DESTROY = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks", "destroy.yml")
DELETE = os.path.join(REPO_ROOT, "roles", "delete", "tasks", "main.yml")
COMMON = os.path.join(REPO_ROOT, "roles", "common")

# Stub runtime: logs every call, reports a stopped crowdsec container and
# the crowdsec volumes until they are removed. With VOLUMES_IN_USE set,
# `volume rm` fails the way it does when another container mounts them.
STUB = """#!/bin/sh
state="$STUB_STATE"
echo "$*" >> "$state/log"
case "$*" in
  "ps -aq --filter label=com.docker.compose.project=inst")
    [ -f "$state/ctr_gone" ] || echo abc123 ;;
  "rm -f abc123")
    touch "$state/ctr_gone" ;;
  "volume ls -q --filter label=com.docker.compose.project=inst")
    [ -f "$state/vols_gone" ] || printf 'inst_crowdsec-config\\ninst_crowdsec-db\\n' ;;
  "volume rm inst_crowdsec-config inst_crowdsec-db")
    if [ -n "$VOLUMES_IN_USE" ]; then echo "volume is in use" >&2; exit 1; fi
    touch "$state/vols_gone" ;;
esac
exit 0
"""


def _run_destroy(tmp_path, in_use=False):
    state = tmp_path / "state"
    state.mkdir()
    stub = tmp_path / "docker"
    stub.write_text(STUB)
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    inst = tmp_path / "inst"
    inst.mkdir()
    (inst / ".env").write_text("COMPOSE_PROFILES=varnish,internal-db\n")
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {"instance_path": str(inst), "instance_id": "inst",
                 "instance_is_k8s": False,
                 "inspect_command": str(stub),
                 "compose_command": "%s compose" % stub,
                 "canasta_root": REPO_ROOT,
                 "ansible_python_interpreter": sys.executable},
        "tasks": [{"ansible.builtin.include_tasks": DESTROY}],
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               STUB_STATE=str(state),
               ANSIBLE_FILTER_PLUGINS=os.path.join(REPO_ROOT, "filter_plugins"),
               ANSIBLE_LIBRARY=os.path.join(COMMON, "library"),
               ANSIBLE_MODULE_UTILS=os.path.join(COMMON, "module_utils"))
    if in_use:
        env["VOLUMES_IN_USE"] = "1"
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env,
                          cwd=tmp_path)
    log = (state / "log").read_text().splitlines()
    return proc, log


def _index(log, needle):
    return next(i for i, line in enumerate(log) if needle in line)


class TestInactiveProfileResourcesAreRemoved:
    def test_delete_succeeds_and_removes_crowdsec_volumes(self, tmp_path):
        proc, log = _run_destroy(tmp_path)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "volume rm inst_crowdsec-config inst_crowdsec-db" in log

    def test_leftovers_are_removed_after_down_containers_first(self, tmp_path):
        proc, log = _run_destroy(tmp_path)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        down = _index(log, "down -v --remove-orphans")
        ctr_rm = _index(log, "rm -f abc123")
        vol_rm = _index(log, "volume rm inst_crowdsec-config")
        backup_rm = _index(log, "volume rm canasta-backup-inst")
        assert down < ctr_rm < vol_rm < backup_rm

    def test_volume_in_use_fails_before_anything_irreversible(self, tmp_path):
        proc, log = _run_destroy(tmp_path, in_use=True)
        assert proc.returncode != 0
        assert ("Failed to remove volume(s) inst_crowdsec-config"
                in proc.stdout + proc.stderr)
        assert not any("canasta-backup-inst" in line for line in log)
        assert not any(line.startswith("run --rm") for line in log)


class TestBackupScheduleOutlivesFailedDelete:
    def _index(self, top, needle):
        for i, t in enumerate(top):
            if needle.lower() in str(t.get("name", "")).lower():
                return i
        raise AssertionError("no top-level task matching %r" % needle)

    def test_backup_schedule_removed_after_destroy(self):
        with open(DELETE) as f:
            top = yaml.safe_load(f)
        assert (self._index(top, "Destroy containers and volumes")
                < self._index(top, "Remove backup schedule"))
