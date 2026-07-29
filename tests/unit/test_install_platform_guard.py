"""`canasta install` must judge the target's OS, not the controller's.

The play runs with `gather_facts: false` (canasta.yml) and these roles
gather facts further down, inside their install blocks — so
`ansible_system` is undefined where the platform guard sits. The old
fallback, `lookup('pipe', 'uname -s')`, runs on the *controller*, so
driving a Linux target from a Mac produced:

    Error: 'canasta install podman' is for Linux servers only.

The failure also inverts the intent: from a Linux controller the guard
passes no matter what the target runs, which is the case it exists to
catch.
"""

import os

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
TASKS = os.path.join(REPO_ROOT, "roles", "install", "tasks")
GUARDED = ("docker.yml", "podman.yml")


def _tasks(name):
    with open(os.path.join(TASKS, name)) as f:
        return [t for t in (yaml.safe_load(f) or []) if isinstance(t, dict)]


def _names(name):
    return [str(t.get("name", "")) for t in _tasks(name)]


def _guard(name):
    return next(
        (t for t in _tasks(name)
         if "unsupported platforms" in str(t.get("name", ""))), None)


def _probe(name):
    return next(
        (t for t in _tasks(name)
         if "Determine target OS" in str(t.get("name", ""))), None)


class TestTheGuardDoesNotConsultTheController:
    def test_no_lookup_in_the_condition(self):
        for name in GUARDED:
            when = str(_guard(name).get("when", ""))
            assert "lookup(" not in when, (
                "%s decides on the controller's OS, so a Mac operator "
                "cannot install to a Linux host" % name
            )

    def test_it_does_not_rely_on_ungathered_facts(self):
        # ansible_system is not available at this point in the file.
        for name in GUARDED:
            assert "ansible_system" not in str(_guard(name).get("when", ""))


class TestTheOSComesFromTheTarget:
    def test_a_probe_task_exists(self):
        for name in GUARDED:
            assert _probe(name), "%s never asks the target its OS" % name

    def test_the_probe_runs_on_the_target(self):
        # ansible.builtin.command runs on the target; lookup/pipe does not.
        for name in GUARDED:
            assert "ansible.builtin.command" in _probe(name)
            assert "uname" in str(_probe(name)["ansible.builtin.command"])

    def test_the_probe_does_not_escalate(self):
        for name in GUARDED:
            assert _probe(name).get("become") is False, (
                "reading the OS needs no root, and requiring it would fail "
                "before the guard could give a useful message"
            )

    def test_the_guard_uses_the_probe(self):
        for name in GUARDED:
            assert "_target_uname" in str(_guard(name).get("when", ""))

    def test_the_probe_precedes_the_guard(self):
        for name in GUARDED:
            names = _names(name)
            probe_at = names.index("Determine target OS")
            guard_at = next(
                i for i, n in enumerate(names) if "unsupported platforms" in n)
            assert probe_at < guard_at

    def test_the_comparison_tolerates_whitespace(self):
        # command stdout carries a trailing newline.
        for name in GUARDED:
            assert "trim" in str(_guard(name).get("when", ""))
