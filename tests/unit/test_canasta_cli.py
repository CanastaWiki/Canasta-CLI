"""Tests for the canasta.py CLI wrapper."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
import canasta as canasta_cli  # noqa: E402 (the canasta.py module)


@pytest.fixture
def data():
    """Load command definitions."""
    return canasta_cli.load_definitions()


@pytest.fixture
def parser(data):
    """Build the argparse parser."""
    return canasta_cli.build_parser(data)


class TestLoadDefinitions:
    def test_loads_commands(self, data):
        assert "commands" in data
        assert len(data["commands"]) > 0

    def test_loads_global_flags(self, data):
        assert "global_flags" in data
        names = [f["name"] for f in data["global_flags"]]
        assert "host" in names
        assert "verbose" in names


class TestBuildParser:
    def test_top_level_commands(self, parser):
        # Should parse top-level commands
        args = parser.parse_args(["version"])
        assert args.command == "version"

    def test_subcommand_group(self, parser):
        args = parser.parse_args(["config", "get", "-i", "mysite"])
        assert args.command == "config"
        assert args.subcommand == "get"
        assert args.id == "mysite"

    def test_nested_subcommand(self, parser):
        args = parser.parse_args([
            "backup", "schedule", "set", "-i", "mysite",
            "0 2 * * *"
        ])
        assert args.command == "backup"
        assert args.subcommand == "schedule"
        assert args.nested_subcommand == "set"

    def test_boolean_flag(self, parser):
        args = parser.parse_args(["delete", "-i", "mysite", "--yes"])
        assert args.yes is True

    def test_boolean_flag_default_false(self, parser):
        args = parser.parse_args(["delete", "-i", "mysite"])
        assert args.yes is False

    def test_string_flag_with_value(self, parser):
        args = parser.parse_args(["create", "-i", "mysite", "-w", "main"])
        assert args.id == "mysite"
        assert args.wiki == "main"

    def test_choice_flag(self, parser):
        args = parser.parse_args([
            "create", "-i", "mysite", "-w", "main",
            "-o", "kubernetes"
        ])
        assert args.orchestrator == "kubernetes"

    def test_invalid_choice_rejected(self, parser):
        with pytest.raises(SystemExit):
            parser.parse_args([
                "create", "-i", "mysite", "-w", "main",
                "-o", "invalid"
            ])

    def test_positional_argument(self, parser):
        args = parser.parse_args(["config", "get", "-i", "mysite", "KEY"])
        assert args.key == "KEY"

    def test_positional_argument_optional(self, parser):
        args = parser.parse_args(["config", "get", "-i", "mysite"])
        assert args.key is None

    def test_short_flags(self, parser):
        args = parser.parse_args(["create", "-i", "mysite", "-w", "main",
                                   "-n", "example.com"])
        assert args.id == "mysite"
        assert args.wiki == "main"
        assert args.domain_name == "example.com"

    def test_long_flags_with_hyphens(self, parser):
        args = parser.parse_args([
            "create", "-i", "mysite", "-w", "main",
            "--domain-name", "example.com",
            "--keep-config"
        ])
        assert args.domain_name == "example.com"
        assert args.keep_config is True

    def test_gitops_fix_submodules(self, parser):
        args = parser.parse_args([
            "gitops", "fix-submodules", "-i", "mysite"
        ])
        assert args.command == "gitops"
        assert args.subcommand == "fix-submodules"

    def test_flag_value_with_equals(self, parser):
        """Values with special chars work via --flag=value syntax."""
        args = parser.parse_args([
            "create", "-i", "mysite", "-w", "main",
            "--domain-name=example.com"
        ])
        assert args.domain_name == "example.com"

    def test_long_field_override(self, parser):
        """Parameters with 'long' field use that as the CLI flag name."""
        args = parser.parse_args([
            "gitops", "init", "-i", "mysite",
            "--name", "prod", "--repo", "git@example.com:org/cfg.git",
            "--key", "/tmp/gc.key"
        ])
        assert args.host_name == "prod"

    def test_long_field_short_flag(self, parser):
        """Short flag still works with long field override."""
        args = parser.parse_args([
            "gitops", "init", "-i", "mysite",
            "-n", "prod", "--repo", "git@example.com:org/cfg.git",
            "--key", "/tmp/gc.key"
        ])
        assert args.host_name == "prod"


class TestResolveCommandName:
    def test_simple_command(self, parser):
        args = parser.parse_args(["version"])
        assert canasta_cli.resolve_command_name(args) == "version"

    def test_subcommand(self, parser):
        args = parser.parse_args(["config", "get"])
        assert canasta_cli.resolve_command_name(args) == "config_get"

    def test_nested_subcommand(self, parser):
        args = parser.parse_args(["backup", "schedule", "set", "* * * * *"])
        name = canasta_cli.resolve_command_name(args)
        assert name == "backup_schedule_set"

    def test_hyphenated_subcommand(self, parser):
        args = parser.parse_args(["gitops", "fix-submodules"])
        name = canasta_cli.resolve_command_name(args)
        assert name == "gitops_fix_submodules"



class TestGlobalFlags:
    def test_verbose_before_command(self):
        """Global flags before command should work via two-pass parse."""
        from argparse import ArgumentParser
        global_parser = ArgumentParser(add_help=False)
        global_parser.add_argument("--verbose", "-v",
                                    action="store_true", default=False)
        global_parser.add_argument("--host", "-H", default=None)

        global_args, remaining = global_parser.parse_known_args(
            ["-v", "version"]
        )
        assert global_args.verbose is True
        assert remaining == ["version"]

    def test_verbose_after_command(self):
        from argparse import ArgumentParser
        global_parser = ArgumentParser(add_help=False)
        global_parser.add_argument("--verbose", "-v",
                                    action="store_true", default=False)
        global_parser.add_argument("--host", "-H", default=None)

        global_args, remaining = global_parser.parse_known_args(
            ["version", "-v"]
        )
        assert global_args.verbose is True
        assert remaining == ["version"]

    def test_host_with_value(self):
        from argparse import ArgumentParser
        global_parser = ArgumentParser(add_help=False)
        global_parser.add_argument("--verbose", "-v",
                                    action="store_true", default=False)
        global_parser.add_argument("--host", "-H", default=None)

        global_args, remaining = global_parser.parse_known_args(
            ["--host", "prod1", "start", "-i", "mysite"]
        )
        assert global_args.host == "prod1"
        assert remaining == ["start", "-i", "mysite"]


class TestBuildAnsibleArgs:
    def _get_vars(self, result):
        """Extract extra vars from the JSON file referenced in -e @file."""
        import json
        for i, arg in enumerate(result):
            if arg == "-e" and i + 1 < len(result) and result[i + 1].startswith("@"):
                with open(result[i + 1][1:]) as f:
                    return json.load(f)
        return {}

    def test_basic_command(self, data):
        from argparse import Namespace
        args = Namespace(
            command="version", host=None, verbose=False,
        )
        result = canasta_cli.build_ansible_args(
            "/usr/bin/ansible-playbook", "version", args, data
        )
        assert result[0] == "/usr/bin/ansible-playbook"
        extra = self._get_vars(result)
        assert extra["command"] == "version"

    def test_host_flag(self, data):
        from argparse import Namespace
        args = Namespace(
            command="start", host="prod1", verbose=False, id="mysite",
        )
        result = canasta_cli.build_ansible_args(
            "ap", "start", args, data
        )
        extra = self._get_vars(result)
        assert extra["target_host"] == "prod1"
        assert "--limit" in result
        assert "prod1" in result

    def test_verbose_flag(self, data):
        from argparse import Namespace
        args = Namespace(
            command="version", host=None, verbose=True,
        )
        result = canasta_cli.build_ansible_args(
            "ap", "version", args, data
        )
        extra = self._get_vars(result)
        assert extra["verbose"] == "true"

    def test_boolean_param(self, data):
        from argparse import Namespace
        args = Namespace(
            command="delete", host=None, verbose=False,
            id="mysite", yes=True,
        )
        result = canasta_cli.build_ansible_args(
            "ap", "delete", args, data
        )
        extra = self._get_vars(result)
        assert extra["yes"] == "true"

    def test_string_param(self, data):
        from argparse import Namespace
        args = Namespace(
            command="start", host=None, verbose=False,
            id="mysite",
        )
        result = canasta_cli.build_ansible_args(
            "ap", "start", args, data
        )
        extra = self._get_vars(result)
        assert extra["id"] == "mysite"

    def test_host_name_param(self, data):
        """host_name parameter (with long: name) is passed correctly."""
        from argparse import Namespace
        args = Namespace(
            command="gitops", subcommand="init",
            host=None, verbose=False,
            id="mysite", host_name="prod",
            role="both", repo="git@example.com:org/cfg.git",
            key="/tmp/gc.key", force=False, pull_requests=False,
        )
        result = canasta_cli.build_ansible_args(
            "ap", "gitops_init", args, data
        )
        extra = self._get_vars(result)
        assert extra["host_name"] == "prod"


class TestRemainderArgs:
    """Test that exec_args/script_args consume flags after command."""

    def test_exec_args_with_flag(self, parser):
        """php -v should not be consumed as --verbose."""
        args = parser.parse_args([
            "maintenance", "exec", "-i", "mysite", "php", "-v"
        ])
        assert args.exec_args == ["php", "-v"]

    def test_exec_args_multiple_flags(self, parser):
        args = parser.parse_args([
            "maintenance", "exec", "-i", "mysite",
            "ls", "-la", "/var/www"
        ])
        assert args.exec_args == ["ls", "-la", "/var/www"]

    def test_script_args_with_flag(self, parser):
        args = parser.parse_args([
            "maintenance", "script", "-i", "mysite",
            "rebuildall.php", "--quick"
        ])
        assert args.script_args == ["rebuildall.php", "--quick"]

    def test_exec_args_empty(self, parser):
        args = parser.parse_args([
            "maintenance", "exec", "-i", "mysite"
        ])
        assert args.exec_args == []


class TestPassthrough:
    """Test -- separator pass-through."""

    def test_passthrough_captured(self):
        """Args after -- should be captured as passthrough."""
        raw = ["maintenance", "exec", "-i", "x", "--", "php", "-v"]
        passthrough = ""
        if "--" in raw:
            idx = raw.index("--")
            passthrough = " ".join(raw[idx + 1:])
            raw = raw[:idx]
        assert passthrough == "php -v"
        assert "--" not in raw

    def test_no_passthrough(self):
        raw = ["maintenance", "exec", "-i", "x", "php"]
        passthrough = ""
        if "--" in raw:
            idx = raw.index("--")
            passthrough = " ".join(raw[idx + 1:])
        assert passthrough == ""


class TestGlobalFlagIsolation:
    """Test that global flags only consume from before the command."""

    def _split_args(self, raw_args, data):
        """Helper: replicate canasta.py pre/post command split."""
        cmd_names = {c["name"].split("_")[0]
                     for c in data["commands"]}
        cmd_names |= {canasta_cli.display_name(n)
                      for n in cmd_names}
        global_value_flags = {"--host", "-H"}
        pre_cmd = []
        post_cmd = []
        found_cmd = False
        skip_next = False
        for arg in raw_args:
            if found_cmd:
                post_cmd.append(arg)
            elif skip_next:
                pre_cmd.append(arg)
                skip_next = False
            elif arg in global_value_flags:
                pre_cmd.append(arg)
                skip_next = True
            elif not arg.startswith("-") and arg in cmd_names:
                found_cmd = True
                post_cmd.append(arg)
            else:
                pre_cmd.append(arg)
        return pre_cmd, post_cmd

    def test_v_after_command_not_consumed(self, data):
        """'-v' after subcommand should NOT become --verbose."""
        pre, post = self._split_args(
            ["maintenance", "exec", "-i", "mysite", "php", "-v"],
            data,
        )
        from argparse import ArgumentParser
        gp = ArgumentParser(add_help=False)
        gp.add_argument("--verbose", "-v", action="store_true",
                         default=False)
        gp.add_argument("--host", "-H", default=None)
        global_args, _ = gp.parse_known_args(pre)

        assert global_args.verbose is False
        assert "php" in post
        assert "-v" in post

    def test_v_before_command_consumed(self, data):
        """-v before subcommand SHOULD become --verbose."""
        pre, post = self._split_args(["-v", "version"], data)

        from argparse import ArgumentParser
        gp = ArgumentParser(add_help=False)
        gp.add_argument("--verbose", "-v", action="store_true",
                         default=False)
        gp.add_argument("--host", "-H", default=None)
        global_args, _ = gp.parse_known_args(pre)

        assert global_args.verbose is True

    def test_instance_named_like_command(self, data):
        """Instance ID matching a command name should NOT be
        treated as the command (e.g., -i maintenance)."""
        pre, post = self._split_args(
            ["stop", "-i", "maintenance"], data,
        )
        # "stop" is the command, "maintenance" is the -i value
        assert post[0] == "stop"
        assert "-i" in post
        assert "maintenance" in post
        # pre should be empty (no global flags)
        assert pre == []

    def test_host_value_like_command(self, data):
        """--host value matching a command name should NOT split
        (e.g., --host backup start -i x)."""
        pre, post = self._split_args(
            ["-H", "backup", "start", "-i", "mysite"], data,
        )
        # "backup" is the --host value, "start" is the command
        assert "-H" in pre
        assert "backup" in pre
        assert post[0] == "start"


class TestBuildAnsibleArgsQuoting:
    """Test that values with spaces and special chars are handled."""

    def _get_vars(self, result):
        import json
        for i, arg in enumerate(result):
            if arg == "-e" and i + 1 < len(result) and result[i + 1].startswith("@"):
                with open(result[i + 1][1:]) as f:
                    return json.load(f)
        return {}

    def test_space_in_value_preserved(self, data):
        from argparse import Namespace
        args = Namespace(
            command="maintenance", subcommand="exec",
            host=None, verbose=False,
            id="mysite", wiki=None,
            exec_args=["php", "-v"],
        )
        result = canasta_cli.build_ansible_args(
            "ap", "maintenance_exec", args, data
        )
        extra = self._get_vars(result)
        assert extra["exec_args"] == "php -v"

    def test_special_chars_in_value(self, data):
        """Values with quotes and Jinja2 chars are safely passed via JSON."""
        from argparse import Namespace
        args = Namespace(
            command="config", subcommand="set",
            host=None, verbose=False,
            id="mysite",
            settings=['MY_KEY=value with "quotes" and {{ braces }}'],
        )
        result = canasta_cli.build_ansible_args(
            "ap", "config_set", args, data
        )
        extra = self._get_vars(result)
        assert '"quotes"' in extra["settings"]
        assert "{{ braces }}" in extra["settings"]


class TestHelperFunctions:
    def test_internal_name(self):
        assert canasta_cli.internal_name("fix-submodules") == "fix_submodules"
        assert canasta_cli.internal_name("create") == "create"

    def test_display_name(self):
        assert canasta_cli.display_name("fix_submodules") == "fix-submodules"
        assert canasta_cli.display_name("create") == "create"
