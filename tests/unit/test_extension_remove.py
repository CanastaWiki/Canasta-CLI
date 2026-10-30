"""Guards for `canasta extension|skin remove` task structure: it must back out
what add does — validate names before deleting, disable tolerantly, deinit and
unregister gitops submodules (including .git/modules cleanup), unregister
composer requirements with a --no-dev refresh, commit once, and restart.
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
TASKS = os.path.join(REPO_ROOT, "roles", "extensions_skins", "tasks")
REMOVE = os.path.join(TASKS, "remove.yml")
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


def _cmds(tasks):
    out = []
    for t in _walk(tasks):
        c = t.get("ansible.builtin.command") or t.get("command") or {}
        cmd = c.get("cmd", "") if isinstance(c, dict) else str(c)
        if cmd:
            out.append(cmd)
    return out


class TestKubernetesGuard:
    def test_declared_compose_only(self):
        # canasta.py refuses the command on Kubernetes from this key.
        with open(DEFS) as f:
            commands = {c["name"]: c for c in yaml.safe_load(f)["commands"]}
        for name in ['extension_remove', 'skin_remove']:
            assert commands[name].get("orchestrators") == ['compose'], name


class TestNameValidation:
    def test_validates_before_any_deletion(self):
        # The validation tasks must appear before the removal block so one
        # typo aborts the whole run instead of half-removing the list.
        tasks = _load(REMOVE)
        seen_validation = False
        for t in _walk(tasks):
            name = str(t.get("name", ""))
            when = t.get("when")
            if "Validate" in name and when and "is not match" in str(when):
                seen_validation = True
            if seen_validation:
                continue
            assert "state: absent" not in str(t), (
                "no deletion may precede name validation")
            cmd = ""
            c = t.get("ansible.builtin.command") or t.get("command") or {}
            cmd = c.get("cmd", "") if isinstance(c, dict) else str(c)
            assert "rm -rf" not in cmd and "git rm" not in cmd, (
                "no deletion may precede name validation")

    def test_rejects_path_escapes(self):
        text = open(REMOVE).read()
        assert "^[A-Za-z0-9][A-Za-z0-9_.-]*$" in text, (
            "names must be validated against path escapes before deletion")


class TestSettingsDisabledTolerantly:
    def test_reads_current_settings_before_disable(self):
        text = open(REMOVE).read()
        assert "state: read" in text, (
            "removal must read current settings so never-enabled items "
            "(added --skip-enable) don't hard-fail the disable step")

    def test_disables_via_settings_module(self):
        text = open(REMOVE).read()
        assert "state: disable" in text


class TestSubmoduleBackout:
    def test_deinit_gitrm_and_modules_cleanup(self):
        cmds = " ".join(_cmds(_load(REMOVE)))
        assert "submodule deinit" in cmds, (
            "registered submodules must be deinit'd")
        assert "submodule status" in cmds, (
            "registration must be checked before choosing the backout path")
        assert "rm --force" in cmds, (
            "the submodule must be unregistered via git rm")
        assert ".git/modules/" in cmds, (
            "leftover .git/modules metadata must be cleaned up")

    def test_no_chained_commands(self):
        # 'command' does not invoke a shell; '&&' would reach git as pathspecs.
        for cmd in _cmds(_load(REMOVE)):
            assert "&&" not in cmd.replace("composer update", ""), (
                "command tasks must not chain commands with &&")


class TestPlainBackout:
    def test_deletes_directories_without_git(self):
        text = open(REMOVE).read()
        assert "ansible.builtin.file" in text and "state: absent" in text, (
            "non-gitops instances must have the directory deleted outright")


class TestComposerBackout:
    def test_unregisters_composer_local_json(self):
        text = open(REMOVE).read()
        assert "canasta_composer_local" in text and "state: absent" in text, (
            "removed items' composer.json entries must be unregistered from "
            "config/composer.local.json")

    def test_runs_composer_update_no_dev(self):
        exec_cmds = [(t.get("vars") or {}).get("exec_command", "")
                     for t in _walk(_load(REMOVE))
                     if (t.get("vars") or {}).get("exec_command")]
        assert any("composer update --no-dev" in c for c in exec_cmds), (
            "vendor must be refreshed after unregistering requirements")


class TestGitopsCommit:
    def test_single_commit_with_gpgsign_disabled(self):
        cmds = _cmds(_load(REMOVE))
        assert any("commit.gpgsign=false commit" in c for c in cmds), (
            "removals must be committed for gitops push to distribute")
        staging = [c for c in cmds if "add -A" in c]
        assert staging, "changes must be staged before committing"


class TestRestart:
    def test_restarts_containers(self):
        for t in _walk(_load(REMOVE)):
            inc = t.get("ansible.builtin.include_role") or {}
            if isinstance(inc, dict) and inc.get("tasks_from") == "start.yml":
                break
        else:
            raise AssertionError(
                "containers must be restarted so symlinked code dirs "
                "disappear")


class TestCommandsRegistered:
    def test_both_remove_commands_defined(self):
        data = yaml.safe_load(open(DEFS))
        names = {c["name"] for c in data["commands"]}
        assert {"extension_remove", "skin_remove"} <= names
        playbooks = {c["name"]: c.get("playbook") for c in data["commands"]}
        for cmd in ("extension_remove", "skin_remove"):
            assert os.path.isfile(os.path.join(
                REPO_ROOT, "playbooks", playbooks[cmd])), (
                "%s playbook must exist" % cmd)


def _named(tasks, prefix):
    return [t for t in _walk(tasks) if t.get("name", "").startswith(prefix)]


class TestDisableEveryScope:
    """Deleting the code breaks every wiki that still loads it."""

    def test_reads_global_and_every_wiki(self):
        reads = [t for t in _walk(_load(REMOVE))
                 if (t.get("canasta_settings_yaml") or {}).get("state") == "read"]
        assert reads
        assert "wiki_ids" in reads[0]["loop"]
        assert "['']" in reads[0]["loop"], "the global scope must be included"

    def test_disables_in_each_scope_that_enables_them(self):
        disables = [t for t in _walk(_load(REMOVE))
                    if (t.get("canasta_settings_yaml") or {}).get("state")
                    == "disable"]
        assert disables
        assert disables[0]["loop"] == "{{ _remove_settings.results }}"

    def test_only_present_items_are_disabled(self):
        # An absent name may be a bundled item the operator still uses.
        disable = next(t for t in _walk(_load(REMOVE))
                       if (t.get("canasta_settings_yaml") or {}).get("state")
                       == "disable")
        assert "_remove_disable" in disable["canasta_settings_yaml"]["names"]
        # An override's copy goes, but the bundled one it overrode stays enabled.
        assert disable["vars"]["_remove_disable"] == (
            "{{ _remove_present | difference(_remove_overrides) }}")

    def test_presence_is_known_before_disabling(self):
        names = [t.get("name", "") for t in _walk(_load(REMOVE))]
        probe = names.index("Check which {{ _item_type }} are present")
        disable = names.index(
            "Disable removed {{ _item_type }} wherever they are enabled")
        assert probe < disable

    def test_remove_has_no_wiki_parameter(self):
        data = yaml.safe_load(open(DEFS))
        for c in data["commands"]:
            if c["name"] in ("extension_remove", "skin_remove"):
                assert "wiki" not in {p["name"] for p in c["parameters"]}


class TestScopedStaging:
    def test_never_stages_whole_directories(self):
        for cmd in _cmds(_load(REMOVE)):
            if "add -A" in cmd:
                assert "config\n" not in cmd and not cmd.rstrip().endswith(
                    "config"), "staging all of config/ sweeps in unrelated edits"
                assert "_item_dir | quote }}\n" not in cmd

    def test_removed_dirs_are_unstaged_per_item(self):
        stage = _named(_load(REMOVE), "Stage removed")
        assert stage and stage[0]["loop"] == "{{ _remove_present }}"
        assert "--ignore-unmatch" in _cmds(stage)[0]


class TestOrphanCleanup:
    def test_orphan_module_metadata_is_deleted(self):
        orphan = _named(_load(REMOVE), "Delete orphan module metadata")
        assert orphan
        assert "/.git/modules/" in orphan[0]["ansible.builtin.file"]["path"]
