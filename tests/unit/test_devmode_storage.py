"""Structural coverage for two commands that had none:
`canasta devmode enable/disable` and `canasta storage setup nfs`.

These assert the task structure (guards, generated resources) rather than
runtime behavior. Full runtime coverage needs infra not available in CI
(a local Docker build for devmode's xdebug image; an NFS server for
storage) and is exercised by the hands-on /e2e run instead.
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
DEVMODE_TASKS = os.path.join(REPO_ROOT, "roles", "devmode", "tasks")


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f)


class TestDevmodeGuards:
    """devmode is Compose-and-localhost only: it extracts code and builds an
    xdebug image on the local machine. Both enable and disable must refuse a
    remote instance and check the dev-mode state before doing any work; the
    Kubernetes refusal comes from the command's `orchestrators:` key."""

    def _fail_conditions(self, tasks):
        # Map each ansible.builtin.fail task's name -> its `when` (as a string).
        out = {}
        for t in tasks:
            if not isinstance(t, dict):
                continue
            if "ansible.builtin.fail" in t:
                out[t.get("name", "")] = str(t.get("when", ""))
        return out

    def test_enable_guards(self):
        fails = self._fail_conditions(_load(os.path.join(DEVMODE_TASKS, "enable.yml")))
        # localhost-only
        local = next(c for n, c in fails.items() if "requires local" in n.lower())
        assert "localhost" in local and "!=" in local
        # not already in dev mode
        assert any("already in dev" in n.lower() for n in fails)

    def test_disable_guards(self):
        fails = self._fail_conditions(_load(os.path.join(DEVMODE_TASKS, "disable.yml")))
        local = next(c for n, c in fails.items() if "requires local" in n.lower())
        assert "localhost" in local and "!=" in local
        # must be IN dev mode to disable
        assert any(n for n in fails if "in dev" in n.lower()
                   and "already" not in n.lower())

    def test_empty_canasta_image_falls_back_to_default(self):
        # canasta_env read returns "" for a missing key, so default() alone
        # never fires.
        tasks = _load(os.path.join(DEVMODE_TASKS, "enable.yml"))
        task = next(t for t in tasks if isinstance(t, dict)
                    and t.get("name") == "Set base image")
        expr = task["ansible.builtin.set_fact"]["_dev_base_image"]
        assert "default(canasta_default_image, true)" in expr


class TestDevmodeRegistryUpdate:
    """Toggling dev mode must touch devMode and nothing else.

    canasta_registry state=present rebuilds the whole record from the
    params given, so enable/disable used to have to forward every field
    they wanted kept — and dropped whatever nobody had thought to add
    yet: dockerHost until it was patched in, then composeCommand,
    inspectCommand and buildArgs. state=update merges instead, so the
    fix is to stop repeating fields, not to repeat more of them."""

    def _registry_update(self, tasks):
        for t in tasks:
            if isinstance(t, dict) and "canasta_registry" in t:
                params = t["canasta_registry"]
                if params.get("state") in ("present", "update"):
                    return params
        raise AssertionError("no canasta_registry write task found")

    def _assert_merges_only_dev_mode(self, filename, want):
        params = self._registry_update(
            _load(os.path.join(DEVMODE_TASKS, filename)))
        assert params["state"] == "update", (
            "state=present replaces the record, so every field %s does "
            "not repeat is dropped" % filename
        )
        assert params["dev_mode"] is want
        assert set(params) == {"id", "dev_mode", "state"}, (
            "%s passes fields it does not mean to change; with "
            "state=update they are noise, and each one is a chance to "
            "write back a stale value" % filename
        )

    def test_enable_merges_only_dev_mode(self):
        self._assert_merges_only_dev_mode("enable.yml", True)

    def test_disable_merges_only_dev_mode(self):
        self._assert_merges_only_dev_mode("disable.yml", False)
