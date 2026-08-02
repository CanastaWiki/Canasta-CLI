"""`_container_running` must mean running, not merely present.

check_running.yml is shared. Widening its probe to `ps -a` to help the
start path would change two other callers:

  roles/upgrade/tasks/main.yml       reads it into _was_running, which
                                     decides whether upgrade restarts the
                                     instance afterward
  roles/orchestrator/tasks/delete_cleanup_files.yml

With `-a` a stopped container satisfies the non-empty test, so upgrade
would start an instance that was deliberately down.

The start path wants the same semantics anyway. #1394 is about running
`canasta start` on an instance that is *already running*; skipping when a
container merely exists would make `canasta start` a no-op on a stopped
instance and leave it down.
"""

import os

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
CHECK = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "check_running.yml")
START = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "start.yml")


def _text(path):
    with open(path) as f:
        return f.read()


def _compose_probe_cmd():
    with open(CHECK) as f:
        for task in yaml.safe_load(f):
            facts = task.get("ansible.builtin.set_fact") or {}
            if task.get("name") == "Build check command for orchestrator":
                return facts["_check_cmd"]
    raise AssertionError("compose check command not found")


class TestTheSharedProbeIsRunningOnly:
    def test_the_probe_does_not_list_all_containers(self):
        assert " ps -a " not in _compose_probe_cmd(), (
            "check_running.yml is shared with upgrade and delete; listing "
            "stopped containers makes a deliberately-stopped instance look "
            "running, and upgrade would restart it"
        )

    def test_the_probe_still_filters_to_web(self):
        cmd = _compose_probe_cmd()
        assert "com.docker.compose.service=web" in cmd
        assert "com.docker.compose.project=" in cmd


class TestTheStartFallbacksAgree:
    def test_the_compose_fallbacks_are_running_only(self):
        # The start path ORs these into _container_running, so `-a` here
        # would reintroduce the same confusion the shared probe avoids.
        text = _text(START)
        assert "ps -a -q" not in text, (
            "a stopped-but-present container would make `canasta start` "
            "skip, leaving the instance down"
        )

    def test_the_failure_dump_still_lists_everything(self):
        # Unrelated to readiness: when `up -d` fails, the diagnostic dump
        # should show stopped containers too.
        assert "ps -a" in _text(START)


class TestTheOtherCallersStillExist:
    def test_upgrade_reads_the_shared_probe(self):
        path = os.path.join(REPO_ROOT, "roles", "upgrade", "tasks", "main.yml")
        assert "check_running.yml" in _text(path)

    def test_delete_reads_the_shared_probe(self):
        path = os.path.join(
            REPO_ROOT, "roles", "orchestrator", "tasks",
            "delete_cleanup_files.yml")
        assert "check_running.yml" in _text(path)
