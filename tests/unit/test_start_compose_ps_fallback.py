"""The Ansible start path needs a runtime-independent running check.

check_running.yml asks the native runtime (`docker`/`podman ps`) by
label. Under canasta-docker the CLI runs inside a container that has the
compose command but not necessarily the native runtime binary, so that
probe can come back empty on an instance that is in fact running — and
the start would proceed into podman-compose's name conflict.

The fallbacks ask the compose command itself, which is present wherever
the CLI can start anything at all.
"""

import os

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
START = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "start.yml")


def _compose_block():
    with open(START) as f:
        doc = yaml.safe_load(f)
    for play in doc:
        if "Docker Compose" in str(play.get("name", "")):
            return play["block"]
    raise AssertionError("compose start block not found")


def _named(name):
    for t in _compose_block():
        if t.get("name") == name:
            return t
    raise AssertionError("task not found: %s" % name)


def _index(name):
    for i, t in enumerate(_compose_block()):
        if t.get("name") == name:
            return i
    raise AssertionError("task not found: %s" % name)


WEB = "Check with compose ps -q web"
ALL = "Check with compose ps -q (podman-compose fallback)"
SET = "Set running status from compose ps"


class TestTheFallbacksOnlyRunWhenNeeded:
    def test_the_web_probe_is_skipped_when_already_known_running(self):
        cond = str(_named(WEB)["when"])
        assert "_container_running" in cond and "not" in cond, (
            "the fallback runs even when the native probe already "
            "answered, costing a round trip per start"
        )

    def test_the_second_probe_only_runs_if_the_first_failed(self):
        # podman-compose's `ps` takes no service argument, so the
        # service-scoped form errors there and the unscoped one is the
        # fallback's fallback.
        conds = " ".join(str(c) for c in _named(ALL)["when"])
        assert "_compose_ps_web" in conds


class TestTheFallbacksAreRunningOnly:
    def test_neither_probe_lists_stopped_containers(self):
        for name in (WEB, ALL):
            cmd = _named(name)["ansible.builtin.command"]["cmd"]
            assert " -a " not in cmd, (
                "%s lists stopped containers, so a stopped instance would "
                "be skipped and left down" % name
            )
            assert "ps -q" in cmd


class TestTheProbesCannotFailTheStart:
    def test_both_probes_tolerate_failure(self):
        # A missing or unhappy compose binary must not abort a start that
        # would otherwise work; the probes only refine a default of False.
        for name in (WEB, ALL):
            task = _named(name)
            assert task["failed_when"] is False
            assert task["changed_when"] is False


class TestTheResultIsOredIn:
    def test_it_never_clears_a_positive_native_result(self):
        expr = _named(SET)["ansible.builtin.set_fact"]["_container_running"]
        assert "_container_running" in expr and "or" in expr, (
            "the fallback overwrites the native probe instead of adding "
            "to it, so a running instance could be reported stopped"
        )

    def test_it_runs_after_both_probes(self):
        assert _index(SET) > _index(ALL) > _index(WEB)
