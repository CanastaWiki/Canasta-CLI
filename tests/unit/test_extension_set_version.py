"""Guard for `canasta extension set-version`: it must move a user extension's
submodule to the requested ref and stage the gitlink for `gitops push`, and
refuse a name that isn't a registered submodule.
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
TASK = os.path.join(
    REPO_ROOT, "roles", "extensions_skins", "tasks", "set_version.yml")
DEFS = os.path.join(REPO_ROOT, "meta", "command_definitions.yml")


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f) or []


def _walk(tasks):
    for t in tasks or []:
        if not isinstance(t, dict):
            continue
        yield t
        for nested in ("block", "rescue", "always"):
            if nested in t:
                yield from _walk(t[nested])


def _cmd(t):
    c = t.get("ansible.builtin.command") or t.get("command") or {}
    return c.get("cmd", "") if isinstance(c, dict) else str(c)


def _exec_commands(tasks):
    # exec_command values passed to included exec.yml tasks.
    return [(t.get("vars") or {}).get("exec_command", "")
            for t in _walk(tasks) if (t.get("vars") or {}).get("exec_command")]


def _fail_msgs(tasks):
    out = []
    for t in _walk(tasks):
        f = t.get("ansible.builtin.fail") or t.get("fail") or {}
        if isinstance(f, dict) and f.get("msg"):
            out.append(f["msg"])
    return out


class TestExtensionSetVersion:
    def setup_method(self):
        self.cmds = [_cmd(t) for t in _walk(_load(TASK)) if _cmd(t)]

    def test_verifies_submodule_registration(self):
        assert any("submodule status" in c for c in self.cmds), (
            "set-version must verify the extension is a registered submodule")

    def test_fetches_before_checkout(self):
        assert any("fetch" in c and "origin" in c for c in self.cmds), (
            "set-version must fetch the extension's remote")

    def test_checks_out_the_requested_ref(self):
        assert any("checkout" in c and "{{ ref" in c for c in self.cmds), (
            "set-version must check out the requested ref")

    def test_stages_the_gitlink(self):
        assert any("git add" in c and "_ext_path" in c for c in self.cmds), (
            "set-version must stage the gitlink (git add -- <ext path>) "
            "for gitops push")

    def test_rejects_invalid_name(self):
        fails = [t for t in _walk(_load(TASK))
                 if "ansible.builtin.fail" in t or "fail" in t]
        assert any("is not match" in " ".join(
            t.get("when") if isinstance(t.get("when"), list) else [str(t.get("when"))])
            for t in fails if t.get("when")), (
            "set-version must validate the extension name (reject path escapes)")

    def test_probes_container_for_bundled_extension(self):
        tasks = _load(TASK)
        assert any("/var/www/mediawiki/w/extensions" in c
                   for c in _exec_commands(tasks)), (
            "set-version must probe the container's bundled extensions dir to "
            "distinguish a bundled extension from a typo")

    def test_has_bundled_specific_error(self):
        assert any("bundled" in m.lower() for m in _fail_msgs(_load(TASK))), (
            "set-version must give a bundled-specific error message")

    def test_probe_tolerates_stopped_container(self):
        # A block/rescue guards the container probe so a stopped instance
        # doesn't abort with an opaque error.
        assert any("rescue" in t for t in _walk(_load(TASK))), (
            "the bundled probe must have a rescue for a stopped container")

    def test_run_update_runs_update_php(self):
        assert any("update.php" in c for c in _exec_commands(_load(TASK))), (
            "--run-update must run update.php to apply schema changes")

    def test_consults_extension_json_for_schema_updates(self):
        cmds = [_cmd(t) for t in _walk(_load(TASK))]
        assert any("LoadExtensionSchemaUpdates" in c for c in cmds), (
            "--run-update must consult the new version's extension.json for "
            "LoadExtensionSchemaUpdates to decide whether update.php is needed")

    def test_can_skip_update_when_no_schema_change(self):
        text = open(TASK).read()
        assert "_ext_run_update" in text, (
            "--run-update must be able to skip update.php when the pinned "
            "version registers no schema updates")


class TestCommandRegistered:
    def test_command_defined(self):
        data = yaml.safe_load(open(DEFS))
        names = {c["name"] for c in data["commands"]}
        assert "extension_set_version" in names, (
            "extension_set_version must be defined in command_definitions.yml")
