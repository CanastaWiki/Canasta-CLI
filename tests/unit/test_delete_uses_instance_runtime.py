"""delete has to tear an instance down with the runtime that built it.

Two failures compounded on rootless Podman:

The delete path set instance_id/path/orchestrator from its registry
query but not composeCommand/inspectCommand, so destroy.yml fell back
to the play-scope default and ran `docker compose down` against a
podman host. That removes nothing, and the containers outlived the
instance.

Then the directory cleanup used a plain `find -delete` with
ignore_errors. Rootless Podman maps the container's root to a subuid,
so files the containers wrote are owned by that subuid and the
operator's own rm cannot remove them. The leftovers then blocked
recreating an instance with the same id, because create refuses a
non-empty target directory.
"""

import os

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
DELETE = os.path.join(REPO_ROOT, "roles", "delete", "tasks", "main.yml")


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

    with open(DELETE) as f:
        walk(yaml.safe_load(f))
    return out


def _named(needle):
    return next(
        (t for t in _tasks()
         if needle.lower() in str(t.get("name", "")).lower()), None)


class TestRuntimeComesFromTheRegistry:
    def test_the_facts_are_set(self):
        facts = _named("Set instance facts from registry")
        assert facts, "no task sets instance facts from the registry"
        body = facts["ansible.builtin.set_fact"]
        for key in ("compose_command", "inspect_command"):
            assert key in body, (
                "%s is not taken from the registry, so destroy.yml uses the "
                "default runtime — `docker compose down` against a podman "
                "host removes nothing and the containers survive" % key
            )

    def test_they_come_from_the_instance_record(self):
        body = _named("Set instance facts from registry")["ansible.builtin.set_fact"]
        assert "composeCommand" in str(body["compose_command"])
        assert "inspectCommand" in str(body["inspect_command"])

    def test_they_fall_back_rather_than_blank(self):
        body = _named("Set instance facts from registry")["ansible.builtin.set_fact"]
        # An instance predating the registry fields must not end up with
        # an empty runtime.
        assert "default(compose_command)" in str(body["compose_command"])
        assert "default(inspect_command)" in str(body["inspect_command"])


class TestDirectoryRemovalEntersTheUserNamespace:
    def test_rootless_podman_is_detected(self):
        probe = _named("Detect rootless Podman")
        assert probe, "nothing detects rootless podman before cleanup"
        assert "Rootless" in str(probe["ansible.builtin.command"]["cmd"])
        assert "podman" in str(probe.get("when", "")).lower(), (
            "the probe should only run when the instance uses podman"
        )

    def test_the_prefix_is_only_set_for_rootless(self):
        setter = _named("namespace-aware removal")
        assert setter, "no fact decides whether to use podman unshare"
        expr = str(setter["ansible.builtin.set_fact"]["_delete_unshare"])
        assert "podman unshare" in expr
        assert "'true'" in expr, (
            "the prefix must be gated on podman reporting rootless, not on "
            "podman merely being present — rootful podman needs no unshare"
        )

    def test_both_removals_use_the_prefix(self):
        for name in ("Remove instance directory contents",
                     "Remove empty instance directory"):
            cmd = str(_named(name)["ansible.builtin.command"]["cmd"])
            assert "_delete_unshare" in cmd, (
                "%s cannot remove subuid-owned files without entering the "
                "user namespace" % name
            )


class TestTheWarningExplainsBothCauses:
    def test_it_covers_the_subuid_case(self):
        msg = str(_named("could not be removed")["ansible.builtin.debug"]["msg"])
        assert "podman unshare" in msg, (
            "the old message blamed the working directory, which is wrong "
            "for subuid-owned leftovers and leaves no way forward"
        )

    def test_it_says_why_it_matters(self):
        msg = str(_named("could not be removed")["ansible.builtin.debug"]["msg"])
        assert "recreat" in msg.lower(), (
            "leftovers block reusing the instance id; say so"
        )
