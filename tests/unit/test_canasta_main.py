"""In-process tests for canasta.py main(): dispatch, exits and prompts."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
import canasta  # noqa: E402
import direct_commands  # noqa: E402

_REAL_ACQUIRE = canasta.acquire_instance_lock


class _Execed(Exception):
    """Raised in place of os.execvp so the Ansible hand-off is observable."""


class _Harness:
    def __init__(self):
        self.direct_calls = []
        self.ansible_calls = []
        self.exec_calls = []
        self.direct_result = 0


@pytest.fixture
def harness(monkeypatch, tmp_path):
    h = _Harness()
    monkeypatch.setenv("CANASTA_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("CANASTA_FORCE_ANSIBLE", raising=False)
    monkeypatch.delenv("CANASTA_CONTAINER_RUNTIME", raising=False)
    monkeypatch.setattr(canasta, "acquire_instance_lock", lambda *a: None)
    monkeypatch.setattr(
        canasta, "check_docker_mode_can_reach_runtime", lambda a: None)
    monkeypatch.setattr(canasta, "check_create_precondition", lambda a: None)

    def run_direct(name, args):
        h.direct_calls.append((name, args))
        return h.direct_result

    monkeypatch.setattr(direct_commands, "run_direct_command", run_direct)
    monkeypatch.setattr(canasta, "find_ansible_playbook",
                        lambda: "ansible-playbook")

    def build(playbook, name, args, data):
        h.ansible_calls.append((name, args))
        return [playbook, "canasta.yml"]

    monkeypatch.setattr(canasta, "build_ansible_args", build)

    def fake_execvp(path, argv):
        raise _Execed(argv)

    monkeypatch.setattr(canasta.os, "execvp", fake_execvp)
    monkeypatch.setattr(canasta, "self_update_cli", lambda **kw: None)
    return h


def _run(monkeypatch, argv):
    """Run main() with argv; return the exit code, or 'exec' on hand-off."""
    monkeypatch.setattr(sys, "argv", ["canasta"] + argv)
    try:
        canasta.main()
    except SystemExit as e:
        return e.code
    except _Execed:
        return "exec"
    raise AssertionError("main() returned without exiting or exec'ing")


class TestDirectDispatch:
    def test_direct_result_is_the_exit_code(self, harness, monkeypatch):
        harness.direct_result = 4
        assert _run(monkeypatch, ["start", "-i", "mysite"]) == 4
        assert [c[0] for c in harness.direct_calls] == ["start"]
        assert harness.ansible_calls == []

    def test_fallback_reaches_ansible(self, harness, monkeypatch):
        harness.direct_result = direct_commands.FALLBACK
        assert _run(monkeypatch, ["start", "-i", "mysite"]) == "exec"
        assert [c[0] for c in harness.direct_calls] == ["start"]
        assert [c[0] for c in harness.ansible_calls] == ["start"]

    def test_force_ansible_skips_fast_path(self, harness, monkeypatch):
        monkeypatch.setenv("CANASTA_FORCE_ANSIBLE", "1")
        assert _run(monkeypatch, ["start", "-i", "mysite"]) == "exec"
        assert harness.direct_calls == []
        assert [c[0] for c in harness.ansible_calls] == ["start"]

    def test_force_ansible_ignored_for_direct_only(self, harness, monkeypatch):
        monkeypatch.setenv("CANASTA_FORCE_ANSIBLE", "1")
        assert _run(monkeypatch, ["version"]) == 0
        assert [c[0] for c in harness.direct_calls] == ["version"]
        assert harness.ansible_calls == []

    def test_direct_only_fallback_exits_1(self, harness, monkeypatch, capsys):
        harness.direct_result = direct_commands.FALLBACK
        assert _run(monkeypatch, ["version"]) == 1
        err = capsys.readouterr().err
        assert "command 'version' is direct_only" in err
        assert "no Ansible playbook to fall back to" in err
        assert harness.ansible_calls == []

    def test_handoff_runs_the_named_playbook(self, harness, monkeypatch):
        locks = []
        monkeypatch.setattr(canasta, "acquire_instance_lock",
                            lambda name, args: locks.append(name))
        harness.direct_result = direct_commands.AnsibleHandoff("restart")
        assert _run(monkeypatch, ["rebuild", "-i", "mysite"]) == "exec"
        assert [c[0] for c in harness.direct_calls] == ["rebuild"]
        assert [c[0] for c in harness.ansible_calls] == ["restart"]
        assert harness.ansible_calls[0][1].id == "mysite"
        assert locks == ["rebuild", "restart"]

    def test_handoff_keeps_the_lock_already_held(
            self, harness, monkeypatch, tmp_path):
        (tmp_path / "conf.json").write_text(
            '{"Instances": {"mysite": {"path": "%s", "host": "localhost",'
            ' "orchestrator": "compose"}}}' % tmp_path)
        monkeypatch.setattr(canasta, "acquire_instance_lock",
                            _REAL_ACQUIRE)
        harness.direct_result = direct_commands.AnsibleHandoff("restart")
        assert _run(monkeypatch, ["rebuild", "-i", "mysite"]) == "exec"
        assert [c[0] for c in harness.ansible_calls] == ["restart"]
        assert "rebuild" in (tmp_path / "locks" / "mysite.lock").read_text()

    def test_non_direct_command_goes_to_ansible(self, harness, monkeypatch):
        assert _run(monkeypatch, ["restart", "-i", "mysite"]) == "exec"
        assert harness.direct_calls == []
        assert [c[0] for c in harness.ansible_calls] == ["restart"]


class TestUnknownCommands:
    def test_no_command_exits_1(self, harness, monkeypatch, capsys):
        assert _run(monkeypatch, []) == 1
        assert "usage: canasta" in capsys.readouterr().out

    def test_invalid_command_exits_2_with_suggestion(
            self, harness, monkeypatch, capsys):
        assert _run(monkeypatch, ["strat"]) == 2
        err = capsys.readouterr().err
        assert "invalid choice: 'strat'" in err
        assert "Did you mean 'start'?" in err

    def test_bare_group_lists_subcommands_exits_2(
            self, harness, monkeypatch, capsys):
        assert _run(monkeypatch, ["gitops"]) == 2
        out = capsys.readouterr().out
        assert "status" in out and "diff" in out

    def test_bare_nested_group_exits_2(self, harness, monkeypatch, capsys):
        assert _run(monkeypatch, ["backup", "schedule"]) == 2
        out = capsys.readouterr().out
        assert "set" in out and "list" in out and "remove" in out

    def test_command_missing_from_definitions_exits_1(
            self, harness, monkeypatch, capsys):
        monkeypatch.setattr(canasta, "resolve_command_name",
                            lambda args: "no_such_command")
        assert _run(monkeypatch, ["version"]) == 1
        assert "Unknown command: no_such_command" in capsys.readouterr().err
        assert harness.direct_calls == []


class TestConfirmationPrompt:
    def _answer(self, monkeypatch, value):
        def fake_input(prompt):
            if isinstance(value, BaseException):
                raise value
            return value
        monkeypatch.setattr("builtins.input", fake_input)

    def test_yes_proceeds_with_yes_set(self, harness, monkeypatch):
        self._answer(monkeypatch, "Y ")
        assert _run(monkeypatch, ["delete", "-i", "mysite"]) == "exec"
        assert [c[0] for c in harness.ansible_calls] == ["delete"]
        assert harness.ansible_calls[0][1].yes is True

    @pytest.mark.parametrize("answer", ["", "n", "yes", "no"])
    def test_other_answers_cancel_with_0(
            self, harness, monkeypatch, capsys, answer):
        self._answer(monkeypatch, answer)
        assert _run(monkeypatch, ["delete", "-i", "mysite"]) == 0
        assert "Operation canceled." in capsys.readouterr().out
        assert harness.ansible_calls == []

    @pytest.mark.parametrize("exc", [EOFError(), KeyboardInterrupt()])
    def test_eof_or_interrupt_cancels_with_1(
            self, harness, monkeypatch, capsys, exc):
        self._answer(monkeypatch, exc)
        assert _run(monkeypatch, ["delete", "-i", "mysite"]) == 1
        assert "Operation canceled." in capsys.readouterr().out
        assert harness.ansible_calls == []

    def test_prompt_names_the_instance(self, harness, monkeypatch):
        prompts = []

        def fake_input(prompt):
            prompts.append(prompt)
            return "n"
        monkeypatch.setattr("builtins.input", fake_input)
        _run(monkeypatch, ["delete", "-i", "mysite"])
        assert len(prompts) == 1
        assert "'mysite'" in prompts[0]
        assert prompts[0].endswith("Continue? [y/N] ")

    def test_yes_flag_skips_prompt(self, harness, monkeypatch):
        def fail_input(prompt):
            raise AssertionError("prompted despite --yes")
        monkeypatch.setattr("builtins.input", fail_input)
        assert _run(monkeypatch, ["delete", "-i", "mysite", "--yes"]) == "exec"


class TestOrchestratorNormalization:
    def test_k8s_alias_becomes_kubernetes(self, harness, monkeypatch):
        assert _run(monkeypatch, [
            "create", "-i", "mysite", "-w", "main", "-n", "example.com",
            "-o", "k8s",
            "--skip-argocd-install",
        ]) == "exec"
        assert harness.ansible_calls[0][1].orchestrator == "kubernetes"

    def test_compose_left_alone(self, harness, monkeypatch):
        assert _run(monkeypatch, [
            "create", "-i", "mysite", "-w", "main", "-n", "example.com",
            "-o", "compose",
        ]) == "exec"
        assert harness.ansible_calls[0][1].orchestrator == "compose"


class TestGlobalFlagIsolation:
    """The global --verbose flag only consumes from before the command,
    so a post-command -v (e.g. 'php -v' in passthrough args) isn't
    hijacked."""

    @pytest.fixture
    def exec_args(self, harness, monkeypatch):
        seen = []

        def fake_exec(args):
            seen.append(args)
            raise SystemExit(0)
        monkeypatch.setattr(canasta, "handle_interactive_exec", fake_exec)
        return seen

    def test_v_after_command_not_consumed(self, monkeypatch, exec_args):
        assert _run(monkeypatch, [
            "maintenance", "exec", "-i", "mysite", "php", "-v"]) == 0
        args = exec_args[0]
        assert args.verbose is False
        assert args.id == "mysite"
        assert list(args.exec_args) == ["php", "-v"]

    def test_v_before_command_consumed(self, harness, monkeypatch):
        assert _run(monkeypatch, ["-v", "version"]) == 0
        assert harness.direct_calls[0][1].verbose is True

    def test_long_verbose_after_command_hoisted(self, harness, monkeypatch):
        assert _run(monkeypatch, ["version", "--verbose"]) == 0
        assert harness.direct_calls[0][1].verbose is True

    def test_verbose_after_double_dash_left_alone(
            self, monkeypatch, exec_args):
        assert _run(monkeypatch, [
            "maintenance", "exec", "-i", "mysite", "--",
            "php", "--verbose"]) == 0
        args = exec_args[0]
        assert args.verbose is False
        assert list(args.exec_args) == ["php", "--verbose"]

    def test_instance_named_like_command(self, harness, monkeypatch):
        """An instance ID matching a command name is not the command."""
        assert _run(monkeypatch, ["stop", "-i", "maintenance"]) == 0
        name, args = harness.direct_calls[0]
        assert name == "stop"
        assert args.id == "maintenance"
        assert args.verbose is False


class TestCommandOrchestrators:
    """A command declaring `orchestrators:` is refused on an instance of
    another orchestrator before either the direct or the Ansible path."""

    @staticmethod
    def _register(tmp_path, orchestrator):
        import json
        (tmp_path / "conf.json").write_text(json.dumps({"Instances": {
            "mysite": {"path": str(tmp_path / "mysite"), "host": "localhost",
                       "orchestrator": orchestrator},
        }}))

    def test_direct_command_refused_on_compose(
            self, harness, monkeypatch, tmp_path, capsys):
        self._register(tmp_path, "compose")
        assert _run(monkeypatch, ["scale", "-i", "mysite", "-r", "2"]) == 1
        err = capsys.readouterr().err
        assert ("'canasta scale' is only supported on Kubernetes instances"
                in err)
        assert "PHP-FPM" in err
        assert harness.direct_calls == []

    def test_direct_command_runs_on_k8s_alias(
            self, harness, monkeypatch, tmp_path):
        self._register(tmp_path, "k8s")
        assert _run(monkeypatch, ["scale", "-i", "mysite", "-r", "2"]) == 0
        assert [c[0] for c in harness.direct_calls] == ["scale"]

    @pytest.mark.parametrize("argv,label", [
        (["extension", "add", "X", "-i", "mysite"], "extension add"),
        (["skin", "remove", "X", "-i", "mysite"], "skin remove"),
        (["devmode", "enable", "-i", "mysite"], "devmode enable"),
        (["devmode", "disable", "-i", "mysite"], "devmode disable"),
    ])
    def test_ansible_command_refused_on_kubernetes(
            self, harness, monkeypatch, tmp_path, capsys, argv, label):
        self._register(tmp_path, "kubernetes")
        assert _run(monkeypatch, argv) == 1
        assert ("'canasta %s' is only supported on Compose instances" % label
                in capsys.readouterr().err)
        assert harness.ansible_calls == []

    def test_ansible_command_runs_on_compose(
            self, harness, monkeypatch, tmp_path):
        self._register(tmp_path, "compose")
        assert _run(monkeypatch, ["devmode", "enable", "-i", "mysite"]) \
            == "exec"

    def test_unresolved_instance_left_to_the_command(
            self, harness, monkeypatch, tmp_path):
        self._register(tmp_path, "kubernetes")
        assert _run(monkeypatch, ["devmode", "enable", "-i", "other"]) \
            == "exec"

    def test_note_is_appended(self):
        cmd_def = {"orchestrators": ["compose"],
                   "orchestrators_note": "Use X\n  instead."}
        assert canasta.orchestrator_refusal(
            cmd_def, "devmode_enable", "kubernetes") == (
            "Error: 'canasta devmode enable' is only supported on Compose "
            "instances. Use X instead.")
        assert canasta.orchestrator_refusal(
            cmd_def, "devmode_enable", None) is None
        assert canasta.orchestrator_refusal(
            {}, "devmode_enable", "kubernetes") is None


class TestUpgradeSelfUpdate:
    @pytest.mark.parametrize("argv,dry_run", [
        (["upgrade"], False), (["upgrade", "--dry-run"], True),
    ])
    def test_dry_run_reaches_the_self_update(self, harness, monkeypatch, argv, dry_run):
        seen = []
        monkeypatch.setattr(canasta, "self_update_cli", lambda **kw: seen.append(kw))
        assert _run(monkeypatch, argv) == "exec"
        assert seen == [{"dev": False, "allow_major": False, "dry_run": dry_run}]
