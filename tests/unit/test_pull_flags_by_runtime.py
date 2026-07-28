"""`pull` must not hand Docker-only flags to podman-compose.

--ignore-buildable and --ignore-pull-failures exist only in Docker
Compose v2. podman-compose 1.3.0 rejects them outright:

    podman-compose: error: unrecognized arguments:
    --ignore-buildable --ignore-pull-failures

exiting 2, so `canasta upgrade` could not pull a single image on a
Podman host.

Neither flag needs a podman counterpart: podman-compose already skips
services with a `build:` section unless --force-local is passed, and it
has no tolerant-pull option at all.
"""

import os

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
PULL = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "pull.yml")

DOCKER_ONLY = ("--ignore-buildable", "--ignore-pull-failures")


def _tasks():
    with open(PULL) as f:
        return [t for t in (yaml.safe_load(f) or []) if isinstance(t, dict)]


def _named(needle):
    return next(
        (t for t in _tasks()
         if needle.lower() in str(t.get("name", "")).lower()), None)


class TestTheFlagsAreNotHardcoded:
    def test_the_pull_command_has_no_literal_docker_flags(self):
        cmd = str(_named("Pull images")["vars"]["rx_cmd"])
        for flag in DOCKER_ONLY:
            assert flag not in cmd, (
                "%s is passed unconditionally; podman-compose exits 2 on it "
                "and nothing is pulled" % flag
            )

    def test_the_command_interpolates_the_compat_fact(self):
        assert "_pull_compat_flags" in str(
            _named("Pull images")["vars"]["rx_cmd"])


class TestTheFlagsAreChosenByRuntime:
    def _expr(self):
        return str(
            _named("Select pull compatibility flags")
            ["ansible.builtin.set_fact"]["_pull_compat_flags"])

    def test_the_selector_exists(self):
        assert _named("Select pull compatibility flags"), (
            "nothing decides the flags per runtime"
        )

    def test_docker_still_gets_both_flags(self):
        for flag in DOCKER_ONLY:
            assert flag in self._expr(), (
                "%s is dropped for Docker too, which changes pull behavior "
                "on the runtime that supports it" % flag
            )

    def test_podman_gets_none(self):
        expr = self._expr()
        assert "podman" in expr, "the choice is not keyed on the runtime"
        # The empty branch must be the podman one, not the reverse.
        podman_at = expr.index("podman")
        empty_at = expr.index("''")
        assert empty_at < podman_at, (
            "the expression reads as `'' if podman else <flags>`; inverting "
            "it would send the flags exactly where they fail"
        )

    def test_it_runs_before_the_pull(self):
        names = [str(t.get("name", "")) for t in _tasks()]
        select_at = next(
            i for i, n in enumerate(names) if "Select pull compatibility" in n)
        pull_at = next(i for i, n in enumerate(names) if "Pull images" in n)
        assert select_at < pull_at

    def test_it_is_not_gated_on_podman(self):
        # A `when: podman` guard would leave the fact undefined for Docker.
        task = _named("Select pull compatibility flags")
        assert "when" not in task, (
            "gating the selector leaves _pull_compat_flags undefined on "
            "Docker, and the pull command references it directly"
        )
