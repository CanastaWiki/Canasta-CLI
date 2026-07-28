"""A start that leaves nothing running must not report success.

`up -d` exiting 0 does not mean the stack is up. On rootless Podman
without lingering, systemd reaps the containers the moment the session
that started them ends — so `canasta create` printed "Done." and exited
0 against an instance whose every container was already dead.

The health gate below could not catch it: it is guarded on the web
container id being non-empty, so "no container at all" read as "nothing
to wait for" and was skipped.
"""

import os

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
START = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks", "start.yml")
PREFLIGHT = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "create_preflight.yml")


def _tasks(path):
    out = []

    def walk(node):
        if isinstance(node, dict):
            out.append(node)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for i in node:
                walk(i)

    with open(path) as f:
        walk(yaml.safe_load(f))
    return out


def _names(path):
    return [str(t.get("name", "")) for t in _tasks(path) if isinstance(t, dict)]


class TestStartFailsWhenNothingIsRunning:
    def _task(self):
        return next(
            (t for t in _tasks(START)
             if "no web container is running" in str(t.get("name", ""))),
            None,
        )

    def test_the_check_exists(self):
        assert self._task(), (
            "start reports success without confirming anything is running"
        )

    def test_it_triggers_on_an_empty_container_id(self):
        when = str(self._task().get("when", ""))
        assert "_start_web_cid.stdout" in when and "== ''" in when, (
            "an empty `ps` result is the signal that nothing is up"
        )

    def test_it_fails_rather_than_warns(self):
        assert "ansible.builtin.fail" in self._task(), (
            "a debug message would still leave the run reporting success"
        )

    def test_it_runs_before_the_health_gate(self):
        names = _names(START)
        get_at = next(i for i, n in enumerate(names) if n == "Get web container id")
        check_at = next(
            i for i, n in enumerate(names) if "no web container is running" in n)
        probe_at = next(
            i for i, n in enumerate(names) if "declares a healthcheck" in n)
        assert get_at < check_at < probe_at, (
            "the check belongs between reading the container id and the "
            "health probe that is skipped when it is empty"
        )

    def test_the_message_points_at_lingering(self):
        msg = str(self._task()["ansible.builtin.fail"]["msg"])
        assert "linger" in msg.lower(), (
            "rootless Podman is the common cause; name it rather than "
            "leaving the operator to discover systemd reaped the stack"
        )


class TestRootlessPodmanPrerequisites:
    def _guard(self):
        return next(
            (t for t in _tasks(PREFLIGHT)
             if "rootless Podman prerequisites" in str(t.get("name", ""))),
            None,
        )

    def test_the_guard_exists(self):
        assert self._guard(), (
            "nothing checks the two rootless-Podman prerequisites, so a "
            "create succeeds and the instance dies immediately"
        )

    def test_it_only_applies_to_podman(self):
        assert "podman" in str(self._guard().get("when", "")).lower()

    def test_it_checks_lingering(self):
        body = yaml.dump(self._guard())
        assert "Linger" in body, "lingering is not checked"
        assert "enable-linger" in body, "say how to fix it"

    def test_it_checks_the_privileged_port_floor(self):
        body = yaml.dump(self._guard())
        assert "ip_unprivileged_port_start" in body, (
            "rootless podman cannot bind 80/443 above the floor; caddy "
            "silently stays in Created"
        )
        assert "HTTP_PORT" in body, (
            "offer the alternative of running on unprivileged ports"
        )

    def test_both_checks_fail_rather_than_warn(self):
        fails = [
            t for t in self._guard().get("block", [])
            if "ansible.builtin.fail" in t
        ]
        assert len(fails) >= 2, (
            "both prerequisites leave a non-functional instance, so both "
            "should stop the create rather than print a warning"
        )
