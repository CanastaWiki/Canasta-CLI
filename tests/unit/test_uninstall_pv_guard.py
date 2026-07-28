"""Removing a k3s worker must not silently delete volumes stored on it.

A local-path PersistentVolume lives on the node that provisioned it, and
`k3s-uninstall.sh` removes /var/lib/rancher/k3s wholesale — so detaching
a worker destroys any volume bound there. This is the common case, not
an exotic one: a Canasta instance's database uses local-path by default
and lands wherever the scheduler put it.

This actually happened: `canasta uninstall k8s --host small --cp-host
big` reported success and took a live instance's database with it. The
only symptom afterwards was a Pending pod whose message said nothing
about the node having been removed on purpose.
"""

import os

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
UNINSTALL = os.path.join(
    REPO_ROOT, "roles", "install", "tasks", "uninstall_k3s.yml")
DEFINITIONS = os.path.join(REPO_ROOT, "meta", "command_definitions.yml")


def _tasks():
    with open(UNINSTALL) as f:
        return yaml.safe_load(f) or []


def _names():
    return [str(t.get("name", "")) for t in _tasks() if isinstance(t, dict)]


def _guard():
    return next(
        (t for t in _tasks()
         if isinstance(t, dict) and "Guard volumes" in str(t.get("name", ""))),
        None,
    )


class TestGuardExists:
    def test_a_guard_task_is_present(self):
        assert _guard(), (
            "nothing checks for volumes pinned to the worker before the "
            "uninstall destroys them"
        )

    def test_it_only_applies_to_workers(self):
        # A control-plane uninstall discards the whole cluster by design.
        assert "worker" in str(_guard().get("when", ""))


class TestGuardRunsBeforeTheDestruction:
    def test_it_precedes_the_uninstall_script(self):
        names = _names()
        guard_at = next(i for i, n in enumerate(names) if "Guard volumes" in n)
        uninstall_at = next(i for i, n in enumerate(names) if n == "Uninstall k3s")
        assert guard_at < uninstall_at, (
            "the guard runs after the uninstall — by then the data is gone"
        )

    def test_it_uses_the_hostname_captured_before_uninstall(self):
        # Node names come from the OS hostname, captured earlier in the
        # file; asking the node itself afterwards would be too late.
        names = _names()
        capture_at = next(
            i for i, n in enumerate(names) if "Capture worker hostname" in n)
        guard_at = next(i for i, n in enumerate(names) if "Guard volumes" in n)
        assert capture_at < guard_at


class TestGuardBehavior:
    def _subtasks(self):
        return _guard().get("block", [])

    def test_it_refuses_by_default(self):
        fail = next(
            (t for t in self._subtasks()
             if "ansible.builtin.fail" in t), None)
        assert fail, "the guard warns but never refuses"
        when = " ".join(fail.get("when", [])) if isinstance(
            fail.get("when"), list) else str(fail.get("when"))
        assert "_doomed_volumes | length > 0" in when
        assert "force" in when, "there must be an escape hatch"

    def test_the_message_names_the_volumes(self):
        fail = next(t for t in self._subtasks() if "ansible.builtin.fail" in t)
        msg = str(fail["ansible.builtin.fail"]["msg"])
        assert "_doomed_volumes | join" in msg, (
            "say which volumes would be lost, not just that some would be"
        )
        assert "--force" in msg, "point at the way forward"

    def test_force_still_warns(self):
        msgs = [
            str(t.get("ansible.builtin.debug", {}).get("msg", ""))
            for t in self._subtasks()
        ]
        assert any("force" in m and "_doomed_volumes" in m for m in msgs), (
            "proceeding under --force should still say what is being destroyed"
        )

    def test_matching_is_not_position_dependent(self):
        # The remote jsonpath is a folded scalar, so YAML injects spaces
        # into the output. Anchored matching silently found nothing.
        setter = next(
            t for t in self._subtasks()
            if "ansible.builtin.set_fact" in t
            and "_doomed_volumes" in str(t["ansible.builtin.set_fact"])
        )
        expr = str(setter["ansible.builtin.set_fact"]["_doomed_volumes"])
        assert "trim" in expr, (
            "lines must be trimmed — folding the jsonpath pads them"
        )
        assert "\\t" not in expr, (
            "do not match on a literal tab; the folded scalar turns the "
            "separator into arbitrary whitespace"
        )


class TestForceFlagIsDeclared:
    def test_uninstall_accepts_force(self):
        with open(DEFINITIONS) as f:
            defs = yaml.safe_load(f)
        cmd = next(c for c in defs["commands"] if c["name"] == "uninstall")
        params = [p["name"] for p in cmd.get("parameters", [])]
        assert "force" in params, (
            "the guard tells the operator to pass --force, so the command "
            "has to accept it"
        )
