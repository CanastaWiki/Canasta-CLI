"""`canasta reconcile` recreates the database when my.cnf changed after it
started.

Compose bind-mounts my.cnf as a single file, so `docker compose up -d` never
recreates the db container for a content change, and MariaDB reads the file
only at startup. reconcile compares my.cnf's mtime with the running db
container's start time and recreates just that service when the file is
newer. A restart would not do: the container's single-file bind mount holds
the inode my.cnf had at creation, and an editor that saves by replacing the
file leaves the container reading the old content. When the start time cannot be read, it says a restart may still be
needed instead of reporting the instance in sync.
"""

import datetime
import json
import os
import stat
import subprocess
import sys

import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "filter_plugins"))
from canasta_containers import container_started_epoch  # noqa: E402
TASK = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks",
                    "restart_db_for_mycnf.yml")
RECONCILE = os.path.join(REPO_ROOT, "roles", "instance_lifecycle", "tasks",
                         "reconcile.yml")
COMMON = os.path.join(REPO_ROOT, "roles", "common")

# Stands in for docker: answers ps and inspect, and logs each call. `ps -a`
# runs only after the recreate, so it reports the new container.
FAKE_DOCKER = """#!/bin/sh
echo "$*" >> "$FAKE_LOG"
case "$1" in
  ps) if [ "$2" = "-a" ]; then echo new456; else printf '%s\\n' "$FAKE_CID"; fi ;;
  inspect)
    case "$3" in
      *StartedAt*) printf '%s\\n' "$FAKE_STARTED" ;;
      *Health*) echo healthy ;;
    esac ;;
esac
"""

FAKE_COMPOSE = """#!/bin/sh
echo "COMPOSE $*" >> "$FAKE_LOG"
"""


class TestContainerStartedEpoch:
    def test_docker_rfc3339(self):
        assert container_started_epoch(
            "2026-10-13T12:00:00.250000000Z") == 1791892800.25

    def test_podman_go_time_with_offset(self):
        assert container_started_epoch(
            "2026-10-13 08:00:00.5 -0400 EDT") == 1791892800.5

    def test_offset_with_colon(self):
        assert container_started_epoch(
            "2026-10-13T14:00:00+02:00") == 1791892800

    def test_unreadable_values(self):
        for value in ("", None, "garbage", "2026-10-13 12:00:00"):
            assert container_started_epoch(value) is None


def _iso(epoch):
    return datetime.datetime.fromtimestamp(
        epoch, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _run(tmp_path, mtime, started, cid="abc123", mycnf=True,
         compose_name="docker-compose", files=(), env_text=None):
    inst = tmp_path / "inst"
    inst.mkdir()
    for name in files:
        (inst / name).write_text("services: {}\n")
    if env_text is not None:
        (inst / ".env").write_text(env_text)
    if mycnf:
        (inst / "my.cnf").write_text("[mysqld]\ninnodb_buffer_pool_size = 256M\n")
        os.utime(inst / "my.cnf", (mtime, mtime))
    docker = tmp_path / "docker"
    docker.write_text(FAKE_DOCKER)
    docker.chmod(docker.stat().st_mode | stat.S_IEXEC)
    compose = tmp_path / compose_name
    compose.write_text(FAKE_COMPOSE)
    compose.chmod(compose.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "calls.log"
    log.write_text("")
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {"instance_path": str(inst), "canasta_root": REPO_ROOT,
                 "instance_is_k8s": False, "inspect_command": str(docker),
                 "compose_command": str(compose),
                 "ansible_python_interpreter": sys.executable},
        "tasks": [
            {"ansible.builtin.include_tasks": TASK},
            {"ansible.builtin.debug": {
                "msg": "UNKNOWN={{ _mycnf_restart_unknown | to_json }}"}},
        ],
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               FAKE_LOG=str(log), FAKE_CID=cid, FAKE_STARTED=started,
               ANSIBLE_FILTER_PLUGINS=os.path.join(REPO_ROOT, "filter_plugins"),
               ANSIBLE_LIBRARY=os.path.join(COMMON, "library"),
               ANSIBLE_MODULE_UTILS=os.path.join(COMMON, "module_utils"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env,
                          cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    restarted = any(line.startswith("COMPOSE ")
                    and line.endswith("up -d --force-recreate --no-deps db")
                    for line in log.read_text().splitlines())
    unknown = json.loads(proc.stdout.split("UNKNOWN=", 1)[1].split('"', 1)[0]
                         .replace("\\", ""))
    return restarted, unknown, log.read_text()


START = 1791892800.0


class TestRestartDecision:
    def test_mycnf_changed_after_start_restarts_db(self, tmp_path):
        restarted, unknown, calls = _run(tmp_path, START + 60, _iso(START))
        assert restarted, calls
        assert "restart" not in calls.replace("--force-recreate", "")
        assert "COMPOSE -f docker-compose.yml up -d" in calls
        assert unknown is False

    def test_health_wait_follows_the_recreated_container(self, tmp_path):
        _, _, calls = _run(tmp_path, START + 60, _iso(START))
        health = [line for line in calls.splitlines()
                  if line.startswith("inspect") and "Health" in line]
        assert health and all(line.endswith(" new456") for line in health)

    def test_recreate_uses_the_layers_start_applies(self, tmp_path):
        _, _, calls = _run(
            tmp_path, START + 60, _iso(START),
            files=("docker-compose.web-env.yml", "docker-compose.override.yml"))
        assert ("COMPOSE -f docker-compose.yml -f docker-compose.web-env.yml "
                "-f docker-compose.override.yml up -d --force-recreate "
                "--no-deps db") in calls

    def test_podman_gets_explicit_profile_flags(self, tmp_path):
        _, _, calls = _run(
            tmp_path, START + 60, _iso(START), compose_name="podman-compose",
            env_text="COMPOSE_PROFILES=varnish,observable\n")
        assert ("COMPOSE -f docker-compose.yml --profile varnish --profile "
                "observable up -d --force-recreate --no-deps db") in calls

    def test_mycnf_older_than_start_leaves_db_alone(self, tmp_path):
        restarted, unknown, calls = _run(tmp_path, START - 60, _iso(START))
        assert not restarted, calls
        assert unknown is False

    def test_subsecond_ordering_is_respected(self, tmp_path):
        # A converge that started db just after the render must not bounce it.
        restarted, _, calls = _run(tmp_path, START + 0.2, _iso(START + 0.7))
        assert not restarted, calls

    def test_unreadable_start_time_is_reported_not_guessed(self, tmp_path):
        restarted, unknown, calls = _run(tmp_path, START + 60, "garbage")
        assert not restarted, calls
        assert unknown is True

    def test_no_running_db_is_not_restarted(self, tmp_path):
        restarted, unknown, calls = _run(tmp_path, START + 60, _iso(START),
                                         cid="")
        assert not restarted, calls
        assert unknown is False

    def test_no_mycnf_is_not_compared(self, tmp_path):
        restarted, unknown, calls = _run(tmp_path, START + 60, _iso(START),
                                         mycnf=False)
        assert calls == ""
        assert not restarted and unknown is False


def _tasks_from(task):
    inc = task.get("ansible.builtin.include_role") or {}
    return inc.get("tasks_from", "")


class TestReconcileWiring:
    def _froms(self):
        with open(RECONCILE) as f:
            return [_tasks_from(t) for t in yaml.safe_load(f)]

    def test_reconcile_restarts_db_for_mycnf_after_converge(self):
        froms = self._froms()
        assert "restart_db_for_mycnf.yml" in froms
        assert froms.index("restart_db_for_mycnf.yml") > froms.index("start.yml")

    def test_restart_precedes_the_database_check(self):
        with open(RECONCILE) as f:
            text = f.read()
        assert text.index("restart_db_for_mycnf.yml") < text.index(
            "warn_missing_wiki_databases.yml")

    def test_report_does_not_claim_sync_when_unknown(self):
        with open(RECONCILE) as f:
            report = [t for t in yaml.safe_load(f)
                      if t.get("name") == "Report reconciled"][0]
        assert "_mycnf_restart_unknown" in report["ansible.builtin.debug"]["msg"]
