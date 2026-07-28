"""upgrade has to act on an instance with the runtime that built it.

_upgrade_single.yml read path/orchestrator/devMode/host off the registry
record but not composeCommand/inspectCommand, so every step ran with the
play-scope default. On a rootless Podman host that means `docker compose
pull` against a missing Docker socket, and the podman profile flags
pull.yml builds are never applied.

The fallback cannot reference compose_command itself: upgrade loops over
instances and set_fact persists, so an instance with no recorded runtime
would inherit the previous instance's podman command.
"""

import os

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
SINGLE = os.path.join(REPO_ROOT, "playbooks", "_upgrade_single.yml")
UPGRADE = os.path.join(REPO_ROOT, "playbooks", "upgrade.yml")


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


def _named(path, needle):
    return next(
        (t for t in _tasks(path)
         if needle.lower() in str(t.get("name", "")).lower()), None)


def _facts():
    return _named(SINGLE, "Set instance facts")["ansible.builtin.set_fact"]


class TestRuntimeComesFromTheRegistry:
    def test_the_facts_are_set(self):
        for key in ("compose_command", "inspect_command"):
            assert key in _facts(), (
                "%s is not taken from the registry, so the upgrade runs "
                "`docker compose` against a podman host" % key
            )

    def test_they_come_from_the_instance_record(self):
        assert "composeCommand" in str(_facts()["compose_command"])
        assert "inspectCommand" in str(_facts()["inspect_command"])


class TestTheFallbackDoesNotLeakBetweenInstances:
    def test_defaults_are_captured_before_the_loop(self):
        saver = _named(UPGRADE, "Remember the default container runtime")
        assert saver, (
            "nothing preserves the play-scope defaults, so the per-instance "
            "fallback has only the previous iteration's value to fall back to"
        )
        body = saver["ansible.builtin.set_fact"]
        assert body["_upgrade_default_compose_command"] == "{{ compose_command }}"
        assert body["_upgrade_default_inspect_command"] == "{{ inspect_command }}"

    def test_it_runs_before_the_loop(self):
        names = [str(t.get("name", "")) for t in _tasks(UPGRADE)]
        save_at = names.index("Remember the default container runtime")
        loop_at = names.index("Upgrade instances")
        assert save_at < loop_at

    def test_the_fallback_uses_the_preserved_default(self):
        for key, want in (
            ("compose_command", "_upgrade_default_compose_command"),
            ("inspect_command", "_upgrade_default_inspect_command"),
        ):
            expr = str(_facts()[key])
            assert want in expr, (
                "%s falls back to a value the loop overwrites, so a docker "
                "instance following a podman one inherits podman" % key
            )

    def test_the_fallback_is_not_self_referential(self):
        # `default(compose_command)` reads the fact this same task sets.
        assert "default(compose_command)" not in str(
            _facts()["compose_command"]).replace(" ", "")
        assert "default(inspect_command)" not in str(
            _facts()["inspect_command"]).replace(" ", "")
