"""The health gate must skip when the runtime reports no health status.

Runtimes disagree on what a container without usable health data looks
like. Podman 4.9.3 reports a non-nil `.State.Health` whose `Status` is
empty; older Podman omits `.State.Health` entirely; Docker reports a
real status.

The gate used to probe `{{if .State.Health}}has{{end}}` and wait
whenever that matched. On Podman 4.9.3 it matched while the status
stayed empty, so `canasta create` spun for the full 60 x 10s and then
failed on an empty-string comparison that told the operator nothing.

Probing the status itself makes an empty render mean "nothing to wait
on" everywhere, with no per-version knowledge encoded.
"""

import os

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
START = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks", "start.yml")


def _tasks():
    out = []

    def walk(node):
        if isinstance(node, dict):
            out.append(node)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for i in node:
                walk(i)

    with open(START) as f:
        walk(yaml.safe_load(f))
    return [t for t in out if isinstance(t, dict) and "name" in t]


def _task(substring):
    for t in _tasks():
        if substring in str(t.get("name", "")):
            return t
    return None


class TestHealthProbeRendersTheStatus:
    def test_probe_renders_the_status_not_the_struct(self):
        probe = _task("health status")
        assert probe is not None, (
            "start.yml must probe the web container's health status "
            "before waiting on it"
        )
        argv = " ".join(str(a) for a in probe["ansible.builtin.command"]["argv"])
        assert ".State.Health.Status" in argv, (
            "the probe must render the status itself — testing "
            "`{{if .State.Health}}` alone matches on Podman 4.9.3, where "
            "the struct exists but the status is empty"
        )

    def test_probe_tolerates_a_missing_health_struct(self):
        probe = _task("health status")
        argv = " ".join(str(a) for a in probe["ansible.builtin.command"]["argv"])
        assert "if .State.Health" in argv, (
            "the status render must stay guarded by an `if`, or inspect "
            "errors out on runtimes that omit .State.Health entirely"
        )


class TestHealthWaitIsGatedOnANonEmptyStatus:
    def test_wait_skips_when_no_status_is_reported(self):
        wait = _task("Wait for web container to report healthy")
        assert wait is not None
        conditions = [str(c) for c in wait["when"]]
        assert any(
            "_start_web_has_health" in c and "!= ''" in c for c in conditions
        ), (
            "the wait must be gated on a non-empty reported status; "
            "an equality check against a sentinel re-introduces the "
            "10-minute stall on runtimes that report no status"
        )

    def test_wait_still_requires_a_running_container(self):
        wait = _task("Wait for web container to report healthy")
        conditions = [str(c) for c in wait["when"]]
        assert any("_start_web_cid" in c for c in conditions), (
            "the wait must still be gated on a web container existing"
        )

    def test_wait_still_waits_for_healthy(self):
        wait = _task("Wait for web container to report healthy")
        assert "healthy" in str(wait.get("until", "")), (
            "the gate must still wait for 'healthy' — it exists to stop "
            "install.php racing composer"
        )
