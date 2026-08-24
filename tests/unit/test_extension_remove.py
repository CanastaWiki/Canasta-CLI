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
    def test_guard_uses_instance_orchestrator(self):
        text = open(REMOVE).read()
        assert "instance_orchestrator | default('compose') in ['kubernetes', 'k8s']" in text, (
            "remove.yml must refuse kubernetes/k8s via instance_orchestrator")


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
