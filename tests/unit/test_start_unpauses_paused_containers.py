"""`canasta start` unpauses an instance's paused containers.

A paused web is listed by `ps` without -a, so start took the instance for
running, while `ps --filter status=running` left web out, so start ran
`up -d` for it, and compose refuses a paused container.
"""

import os
import stat
import subprocess
import sys

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
TASKS = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks")
UNPAUSE = os.path.join(TASKS, "unpause_services.yml")
START = os.path.join(TASKS, "start.yml")

# Prints $PAUSED for `ps`, records every call, and fails `unpause` when
# $UNPAUSE_RC is non-zero.
FAKE_RUNTIME = """#!/bin/sh
echo "$@" >> "$CALLS"
case "$1" in
  ps) printf '%b' "$PAUSED" ;;
  unpause)
    if [ "${UNPAUSE_RC:-0}" != 0 ]; then
      echo "cannot unpause" >&2
      exit "$UNPAUSE_RC"
    fi ;;
esac
"""


def _run(tmp_path, paused, unpause_rc=0, runtime="docker"):
    inst = tmp_path / "MySite"
    inst.mkdir()
    fake = tmp_path / runtime
    fake.write_text(FAKE_RUNTIME)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    calls = tmp_path / "calls"
    calls.write_text("")
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {
            "instance_path": str(inst),
            "instance_id": "mysite",
            "inspect_command": str(fake),
            "ansible_python_interpreter": sys.executable,
        },
        "tasks": [{"ansible.builtin.include_tasks": UNPAUSE}],
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               CALLS=str(calls), PAUSED=paused, UNPAUSE_RC=str(unpause_rc))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env,
                          cwd=tmp_path)
    return proc, calls.read_text().splitlines()


class TestUnpause:
    def test_paused_containers_are_unpaused_and_reported(self, tmp_path):
        proc, calls = _run(tmp_path, "c1 web\\nc2 db\\n")
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "unpause c1 c2" in calls
        assert "Unpaused db, web." in proc.stdout

    def test_the_listing_is_scoped_to_paused_containers_of_the_project(
            self, tmp_path):
        _, calls = _run(tmp_path, "")
        ps = calls[0]
        assert ps.startswith("ps ")
        assert "status=paused" in ps
        assert "label=com.docker.compose.project=mysite" in ps
        assert '.Label "com.docker.compose.service"' in ps

    def test_podman_reads_the_service_label_its_own_way(self, tmp_path):
        proc, calls = _run(tmp_path, "c1 web\\n", runtime="podman")
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert 'index .Labels "com.docker.compose.service"' in calls[0]
        assert "unpause c1" in calls

    def test_nothing_paused_means_nothing_unpaused(self, tmp_path):
        proc, calls = _run(tmp_path, "")
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert not any(c.startswith("unpause") for c in calls)
        assert "Unpaused" not in proc.stdout

    def test_a_failed_unpause_stops_with_a_plain_message(self, tmp_path):
        proc, _ = _run(tmp_path, "c1 web\\n", unpause_rc=1)
        out = proc.stdout + proc.stderr
        assert proc.returncode != 0
        assert ("The web container of instance 'mysite' is paused and "
                "could not be unpaused: cannot unpause") in " ".join(out.split())


class TestStartUnpausesFirst:
    def _names(self):
        with open(START) as f:
            play = next(p for p in yaml.safe_load(f)
                        if "Docker Compose" in str(p.get("name", "")))
        return [t.get("name") for t in play["block"]]

    def test_it_runs_before_start_decides_what_is_running(self):
        names = self._names()
        unpause = names.index("Unpause paused containers")
        assert unpause < names.index("Check if already running")
        assert unpause < names.index("List the services actually running")
        assert unpause < names.index("Start containers")
