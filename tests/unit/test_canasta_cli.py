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

    def test_migrate(self, parser):
        args = parser.parse_args([
            "migrate", "-i", "mysite",
            "--from", "host1", "--to", "host2", "-y"
        ])
        assert canasta_cli.resolve_command_name(args) == "migrate"

    def test_clone(self, parser):
        args = parser.parse_args([
            "clone", "-i", "mysite",
            "--from", "host1", "--to", "host2",
            "--new-id", "staging", "--new-domain", "s.example.com",
            "-y"
        ])
        assert canasta_cli.resolve_command_name(args) == "clone"


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
    def test_basic_command(self, data):
        from argparse import Namespace
        args = Namespace(
            command="version", host=None, verbose=False,
        )
        result = canasta_cli.build_ansible_args(
            "/usr/bin/ansible-playbook", "version", args, data
        )
        assert result[0] == "/usr/bin/ansible-playbook"
        assert "-e" in result
        assert "command=version" in result

    def test_host_flag(self, data):
        from argparse import Namespace
        args = Namespace(
            command="start", host="prod1", verbose=False, id="mysite",
        )
        result = canasta_cli.build_ansible_args(
            "ap", "start", args, data
        )
        assert "target_host=prod1" in result
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
        assert "verbose=true" in result

    def test_boolean_param(self, data):
        from argparse import Namespace
        args = Namespace(
            command="delete", host=None, verbose=False,
            id="mysite", yes=True,
        )
        result = canasta_cli.build_ansible_args(
            "ap", "delete", args, data
        )
        assert "yes=true" in result

    def test_string_param(self, data):
        from argparse import Namespace
        args = Namespace(
            command="start", host=None, verbose=False,
            id="mysite",
        )
        result = canasta_cli.build_ansible_args(
            "ap", "start", args, data
        )
        assert "id=mysite" in result


class TestHelperFunctions:
    def test_internal_name(self):
        assert canasta_cli.internal_name("fix-submodules") == "fix_submodules"
        assert canasta_cli.internal_name("create") == "create"

    def test_display_name(self):
        assert canasta_cli.display_name("fix_submodules") == "fix-submodules"
        assert canasta_cli.display_name("create") == "create"
