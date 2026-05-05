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
        assert "verbose" in names
        # --host was demoted to a per-command flag in #325; it lives
        # on create/list/version/doctor/install/uninstall now, not
        # in global_flags.
        assert "host" not in names


class TestBuildParser:
    def test_top_level_commands(self, parser):
        # Should parse top-level commands
        args = parser.parse_args(["version"])
        assert args.command == "version"

    def test_version_bare(self, parser):
        args = parser.parse_args(["version"])
        assert args.id is None
        assert args.cli_only is False

    def test_version_with_id(self, parser):
        args = parser.parse_args(["version", "-i", "mysite"])
        assert args.id == "mysite"
        assert args.cli_only is False

    def test_version_with_cli_only_flag(self, parser):
        args = parser.parse_args(["version", "--cli-only"])
        assert args.id is None
        assert args.cli_only is True

    def test_version_with_cli_only_short_flag(self, parser):
        args = parser.parse_args(["version", "-c"])
        assert args.id is None
        assert args.cli_only is True

    def test_list_cleanup_flags(self, parser):
        args = parser.parse_args(["list", "--cleanup"])
        assert args.cleanup is True
        assert args.force is False
        assert args.dry_run is False

    def test_list_cleanup_with_force(self, parser):
        args = parser.parse_args(["list", "--cleanup", "--force"])
        assert args.cleanup is True
        assert args.force is True
        assert args.dry_run is False

    def test_list_cleanup_with_dry_run(self, parser):
        args = parser.parse_args(["list", "--cleanup", "--dry-run"])
        assert args.cleanup is True
        assert args.force is False
        assert args.dry_run is True

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
        args = parser.parse_args([
            "create", "-i", "mysite", "-w", "main", "-n", "example.com"
        ])
        assert args.id == "mysite"
        assert args.wiki == "main"

    def test_choice_flag(self, parser):
        args = parser.parse_args([
            "create", "-i", "mysite", "-w", "main", "-n", "example.com",
            "-o", "kubernetes"
        ])
        assert args.orchestrator == "kubernetes"

    def test_invalid_choice_rejected(self, parser):
        with pytest.raises(SystemExit):
            parser.parse_args([
                "create", "-i", "mysite", "-w", "main",
                "-o", "invalid"
            ])

    def test_config_get_single_positional_key(self, parser):
        args = parser.parse_args(
            ["config", "get", "-i", "mysite", "KEY"],
        )
        assert args.keys == ["KEY"]

    def test_config_get_multiple_positional_keys(self, parser):
        args = parser.parse_args(
            ["config", "get", "-i", "mysite", "KEY1", "KEY2"],
        )
        assert args.keys == ["KEY1", "KEY2"]

    def test_config_get_keys_optional(self, parser):
        args = parser.parse_args(["config", "get", "-i", "mysite"])
        assert args.keys == []

    def test_config_regenerate_subcommand(self, parser):
        args = parser.parse_args(
            ["config", "regenerate", "-i", "mysite"],
        )
        assert args.command == "config"
        assert args.subcommand == "regenerate"
        assert args.id == "mysite"
        assert canasta_cli.resolve_command_name(args) == "config_regenerate"

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

    def test_domain_name_localhost_accepted(self, parser):
        """Local-dev case: -n localhost is a valid value (no implicit default).

        Caddy handles localhost via its internal CA (no ACME), so no extra
        flags are needed.
        """
        args = parser.parse_args([
            "create", "-i", "mysite", "-w", "main", "-n", "localhost"
        ])
        assert args.domain_name == "localhost"

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
        """--verbose before command should be parsed by the global parser."""
        from argparse import ArgumentParser
        global_parser = ArgumentParser(add_help=False)
        global_parser.add_argument("--verbose", "-v",
                                    action="store_true", default=False)

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

        global_args, remaining = global_parser.parse_known_args(
            ["version", "-v"]
        )
        assert global_args.verbose is True
        assert remaining == ["version"]


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

    def test_host_flag_on_create(self, data):
        from argparse import Namespace
        args = Namespace(
            command="create", host="prod1", verbose=False, id="mysite",
            wiki="main", domain_name="example.com", site_name=None,
            database=None, path=None, orchestrator=None,
            admin_password=None, wiki_db_password=None,
            root_db_password=None,
        )
        result = canasta_cli.build_ansible_args(
            "ap", "create", args, data
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

    def test_default_ansible_ssh_args_carries_required_options(
        self, data, monkeypatch,
    ):
        """build_ansible_args plants a default ANSIBLE_SSH_ARGS that has
        to include four things at once for remote operations to work
        without operator ceremony:

        - StrictHostKeyChecking=accept-new so first contact with a new
          host doesn't fail the play.
        - UserKnownHostsFile=~/.ssh/known_hosts so the accepted key
          actually persists for next time.
        - ForwardAgent=yes so the operator's local ssh-agent reaches
          the target host (gitops `git push` to a private repo on a
          remote then authenticates against the forge with the
          operator's keys; see #465).
        - ServerAliveInterval keeps long-running remote commands
          (helm upgrade --wait, gitops init's git push, maintenance
          update) from tripping a NAT or firewall idle drop and
          reporting a bogus "Broken pipe" while the remote is still
          working.

        Guard against any of those silently disappearing.
        """
        monkeypatch.delenv("ANSIBLE_SSH_ARGS", raising=False)
        from argparse import Namespace
        args = Namespace(command="version", host=None, verbose=False)
        canasta_cli.build_ansible_args(
            "/usr/bin/ansible-playbook", "version", args, data,
        )
        ssh_args = os.environ.get("ANSIBLE_SSH_ARGS", "")
        assert "StrictHostKeyChecking=accept-new" in ssh_args
        assert "UserKnownHostsFile=" in ssh_args
        assert "ForwardAgent=yes" in ssh_args
        assert "ServerAliveInterval=" in ssh_args


class TestHostCommandsBehavior:
    """Test that --host is passed through for commands that declare it
    and is rejected by argparse on commands that don't."""

    def _get_vars(self, result):
        import json
        for i, arg in enumerate(result):
            if arg == "-e" and i + 1 < len(result) and result[i + 1].startswith("@"):
                with open(result[i + 1][1:]) as f:
                    return json.load(f)
        return {}

    def test_host_flag_on_list(self, data):
        """--host should be passed through for the 'list' command."""
        from argparse import Namespace
        args = Namespace(
            command="list", host="prod1", verbose=False,
        )
        result = canasta_cli.build_ansible_args(
            "ap", "list", args, data
        )
        extra = self._get_vars(result)
        assert extra["target_host"] == "prod1"
        assert "--limit" in result
        assert "prod1" in result

    @pytest.mark.parametrize("cmd", ["start", "stop", "restart", "add", "delete"])
    def test_host_flag_rejected_by_argparse(self, parser, cmd):
        """Commands that don't declare --host should have argparse
        reject the flag outright — there's no silent 'ignored' fallback
        anymore."""
        argv = [cmd, "--host", "prod1"]
        # Most of these need an -i to parse at all; tack it on.
        if cmd in ("start", "stop", "restart", "delete"):
            argv.extend(["-i", "mysite"])
        elif cmd == "add":
            argv.extend(["-i", "mysite", "-w", "w", "-u", "u"])
        with pytest.raises(SystemExit):
            parser.parse_args(argv)

    def test_host_flag_on_doctor(self, data):
        """-H should be passed through for 'doctor' so users can check a
        remote host's dependencies before creating an instance."""
        from argparse import Namespace
        args = Namespace(
            command="doctor", host="newhost.example.com", verbose=False,
        )
        result = canasta_cli.build_ansible_args(
            "ap", "doctor", args, data
        )
        extra = self._get_vars(result)
        assert extra["target_host"] == "newhost.example.com"
        assert "--limit" in result
        assert "newhost.example.com" in result

    def test_host_flag_absent_on_doctor_runs_locally(self, data):
        """Without -H, doctor runs on localhost (no target_host set)."""
        from argparse import Namespace
        args = Namespace(
            command="doctor", host=None, verbose=False,
        )
        result = canasta_cli.build_ansible_args(
            "ap", "doctor", args, data
        )
        extra = self._get_vars(result)
        assert "target_host" not in extra
        assert "--limit" not in result


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


class TestRemainderFlagHoisting:
    """Canasta flags trapped inside script_args/exec_args by REMAINDER
    should be lifted back out (#279)."""

    def _parse_and_hoist(self, parser, argv):
        args = parser.parse_args(argv)
        canasta_cli.hoist_flags_from_remainder(args)
        return args

    def test_wiki_flag_after_script_name(self, parser):
        args = self._parse_and_hoist(parser, [
            "maintenance", "script", "-i", "mysite",
            "showJobs.php", "-w", "main"
        ])
        assert args.wiki == "main"
        assert args.script_args == ["showJobs.php"]

    def test_long_wiki_flag_after_script_name(self, parser):
        args = self._parse_and_hoist(parser, [
            "maintenance", "script", "-i", "mysite",
            "showJobs.php", "--wiki", "main"
        ])
        assert args.wiki == "main"
        assert args.script_args == ["showJobs.php"]

    def test_wiki_flag_before_script_name_unchanged(self, parser):
        args = self._parse_and_hoist(parser, [
            "maintenance", "script", "-i", "mysite",
            "-w", "main", "showJobs.php"
        ])
        assert args.wiki == "main"
        assert args.script_args == ["showJobs.php"]

    def test_duplicate_wiki_flag_preserves_second_in_passthrough(self, parser):
        # -w main is for canasta; -w other stays in script_args
        # so it can be passed through to the inner script.
        args = self._parse_and_hoist(parser, [
            "maintenance", "script", "-i", "mysite",
            "-w", "main", "myScript.php", "-w", "other"
        ])
        assert args.wiki == "main"
        assert args.script_args == ["myScript.php", "-w", "other"]

    def test_id_flag_after_positional(self, parser):
        args = self._parse_and_hoist(parser, [
            "maintenance", "exec", "php", "-v", "-i", "mysite"
        ])
        assert args.id == "mysite"
        assert args.exec_args == ["php", "-v"]

    def test_exec_args_with_no_canasta_flags(self, parser):
        args = self._parse_and_hoist(parser, [
            "maintenance", "exec", "-i", "mysite", "ls", "-la", "/var/www"
        ])
        assert args.id == "mysite"
        assert args.exec_args == ["ls", "-la", "/var/www"]

    def test_hoist_noop_on_non_remainder_commands(self, parser):
        # config set uses a non-REMAINDER positional; hoist should leave it
        # alone.
        args = self._parse_and_hoist(parser, [
            "config", "set", "-i", "mysite", "KEY=value"
        ])
        assert args.id == "mysite"
        assert args.settings == ["KEY=value"]


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
    """Test that the global --verbose flag only consumes from before
    the command, so a post-command -v (e.g. 'php -v' in passthrough
    args) isn't hijacked."""

    def _split_args(self, raw_args, data):
        """Helper: replicate canasta.py pre/post command split.

        After #325, --host is per-command (not global), so this
        helper only needs to know which tokens are subcommand names.
        """
        cmd_names = {c["name"].split("_")[0]
                     for c in data["commands"]}
        cmd_names |= {canasta_cli.display_name(n)
                      for n in cmd_names}
        pre_cmd = []
        post_cmd = []
        found_cmd = False
        for arg in raw_args:
            if found_cmd:
                post_cmd.append(arg)
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


class TestCreateFlags:
    """Test create command flags for K8s and TLS."""

    def test_skip_tls_accepted_with_compose(self, parser):
        args = parser.parse_args([
            "create", "-i", "mysite", "-w", "main", "-n", "example.com",
            "--skip-tls"
        ])
        assert args.skip_tls is True

    def test_skip_tls_accepted_with_kubernetes(self, parser):
        args = parser.parse_args([
            "create", "-i", "mysite", "-w", "main", "-n", "example.com",
            "-o", "kubernetes", "--skip-tls"
        ])
        assert args.skip_tls is True
        assert args.orchestrator == "kubernetes"

    def test_storage_class_accepted_with_kubernetes(self, parser):
        args = parser.parse_args([
            "create", "-i", "mysite", "-w", "main", "-n", "example.com",
            "-o", "kubernetes", "--storage-class", "nfs"
        ])
        assert args.storage_class == "nfs"

    def test_tls_email_accepted_with_kubernetes(self, parser):
        args = parser.parse_args([
            "create", "-i", "mysite", "-w", "main", "-n", "example.com",
            "-o", "kubernetes", "--tls-email", "test@example.com"
        ])
        assert args.tls_email == "test@example.com"

    def test_service_flag_on_maintenance_exec(self, parser):
        args = parser.parse_args([
            "maintenance", "exec", "-i", "mysite", "-s", "db",
            "mariadb", "--version"
        ])
        assert args.service == "db"
        assert args.exec_args == ["mariadb", "--version"]

    def test_service_flag_default(self, parser):
        args = parser.parse_args([
            "maintenance", "exec", "-i", "mysite"
        ])
        assert args.service is None


class TestOrchestratorValidation:
    """Test that orchestrator_only params are rejected for wrong orchestrator."""

    def test_storage_class_rejected_with_compose(self, data):
        """--storage-class with --orchestrator compose should be rejected."""
        cmd_index = {c["name"]: c for c in data["commands"]}
        cmd_def = cmd_index["create"]
        # Simulate: orchestrator=compose, storage_class=nfs
        orchestrator = "compose"
        for param in cmd_def.get("parameters", []):
            orch_only = param.get("orchestrator_only")
            if not orch_only:
                continue
            if param["name"] == "storage_class":
                assert orch_only == "kubernetes"
                assert orchestrator != orch_only  # would be rejected

    def test_skip_tls_not_orchestrator_restricted(self, data):
        """--skip-tls should work with any orchestrator."""
        cmd_index = {c["name"]: c for c in data["commands"]}
        cmd_def = cmd_index["create"]
        for param in cmd_def.get("parameters", []):
            if param["name"] == "skip_tls":
                assert "orchestrator_only" not in param


class TestHelperFunctions:
    def test_internal_name(self):
        assert canasta_cli.internal_name("fix-submodules") == "fix_submodules"
        assert canasta_cli.internal_name("create") == "create"

    def test_display_name(self):
        assert canasta_cli.display_name("fix_submodules") == "fix-submodules"
        assert canasta_cli.display_name("create") == "create"


class TestPathResolution:
    """Path-type args should be anchored to the caller's CWD, not
    playbook_dir, so 'canasta create -p .' lands in the user's working
    directory instead of the canasta.py install directory."""

    def _get_vars(self, result):
        import json
        for i, arg in enumerate(result):
            if arg == "-e" and i + 1 < len(result) and result[i + 1].startswith("@"):
                with open(result[i + 1][1:]) as f:
                    return json.load(f)
        return {}

    def test_path_dot_resolves_to_cwd(self, data, tmp_path, monkeypatch):
        from argparse import Namespace
        monkeypatch.chdir(tmp_path)
        args = Namespace(
            command="create", host=None, verbose=False, id="mysite",
            wiki="main", domain_name=None, site_name=None,
            database=None, path=".", orchestrator=None,
            admin_password=None, wiki_db_password=None,
            root_db_password=None,
        )
        result = canasta_cli.build_ansible_args("ap", "create", args, data)
        extra = self._get_vars(result)
        assert extra["path"] == str(tmp_path)

    def test_path_absolute_passthrough(self, data):
        from argparse import Namespace
        args = Namespace(
            command="create", host=None, verbose=False, id="mysite",
            wiki="main", domain_name=None, site_name=None,
            database=None, path="/srv/canasta", orchestrator=None,
            admin_password=None, wiki_db_password=None,
            root_db_password=None,
        )
        result = canasta_cli.build_ansible_args("ap", "create", args, data)
        extra = self._get_vars(result)
        assert extra["path"] == "/srv/canasta"

    def test_path_tilde_expands(self, data):
        from argparse import Namespace
        args = Namespace(
            command="create", host=None, verbose=False, id="mysite",
            wiki="main", domain_name=None, site_name=None,
            database=None, path="~", orchestrator=None,
            admin_password=None, wiki_db_password=None,
            root_db_password=None,
        )
        result = canasta_cli.build_ansible_args("ap", "create", args, data)
        extra = self._get_vars(result)
        assert extra["path"] == os.path.expanduser("~")
        assert not extra["path"].startswith("~")


class TestPathKindRemote:
    """With --host, `create --path` is path_kind: remote — default '.'
    becomes the canonical remote default '~/canasta', absolute paths
    pass through, and relative paths are rejected up front instead of
    being silently abspath'd against the laptop (#384)."""

    def _get_vars(self, result):
        import json
        for i, arg in enumerate(result):
            if arg == "-e" and i + 1 < len(result) and result[i + 1].startswith("@"):
                with open(result[i + 1][1:]) as f:
                    return json.load(f)
        return {}

    def test_path_dot_with_host_becomes_remote_canasta(self, data):
        from argparse import Namespace
        args = Namespace(
            command="create", host="cp", verbose=False, id="mysite",
            wiki="main", domain_name=None, site_name=None,
            database=None, path=".", orchestrator=None,
            admin_password=None, wiki_db_password=None,
            root_db_password=None,
        )
        result = canasta_cli.build_ansible_args("ap", "create", args, data)
        extra = self._get_vars(result)
        assert extra["path"] == "~/canasta"

    def test_path_absolute_with_host_passthrough(self, data):
        from argparse import Namespace
        args = Namespace(
            command="create", host="cp", verbose=False, id="mysite",
            wiki="main", domain_name=None, site_name=None,
            database=None, path="/home/admin/canasta", orchestrator=None,
            admin_password=None, wiki_db_password=None,
            root_db_password=None,
        )
        result = canasta_cli.build_ansible_args("ap", "create", args, data)
        extra = self._get_vars(result)
        assert extra["path"] == "/home/admin/canasta"

    def test_path_tilde_with_host_passthrough_unexpanded(self, data):
        """Don't expanduser against the laptop when targeting a remote
        host — Ansible expands ~ on the remote user's home."""
        from argparse import Namespace
        args = Namespace(
            command="create", host="cp", verbose=False, id="mysite",
            wiki="main", domain_name=None, site_name=None,
            database=None, path="~/instances", orchestrator=None,
            admin_password=None, wiki_db_password=None,
            root_db_password=None,
        )
        result = canasta_cli.build_ansible_args("ap", "create", args, data)
        extra = self._get_vars(result)
        assert extra["path"] == "~/instances"

    def test_path_relative_with_host_errors(self, data, capsys):
        from argparse import Namespace
        args = Namespace(
            command="create", host="cp", verbose=False, id="mysite",
            wiki="main", domain_name=None, site_name=None,
            database=None, path="instances/site", orchestrator=None,
            admin_password=None, wiki_db_password=None,
            root_db_password=None,
        )
        with pytest.raises(SystemExit) as exc:
            canasta_cli.build_ansible_args("ap", "create", args, data)
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "instances/site" in err
        assert "absolute" in err.lower()


class TestDidYouMean:
    """canasta.py uses CanastaArgumentParser to augment argparse's
    'invalid choice' error with a 'Did you mean …?' hint when the
    user's typo is close to a valid option."""

    def _run(self, parser, argv, capsys):
        with pytest.raises(SystemExit):
            parser.parse_args(argv)
        return capsys.readouterr().err

    def test_typo_in_subcommand_group_suggests(self, parser, capsys):
        err = self._run(parser, ["argocd", "up"], capsys)
        assert "invalid choice: 'up'" in err
        assert "Did you mean 'ui'?" in err

    def test_typo_in_subcommand_group_misspelling_suggests(self, parser, capsys):
        err = self._run(parser, ["argocd", "pasword"], capsys)
        assert "Did you mean 'password'?" in err

    def test_top_level_typo_suggests(self, parser, capsys):
        err = self._run(parser, ["storrage"], capsys)
        assert "Did you mean 'storage'?" in err

    def test_far_off_typo_no_suggestion(self, parser, capsys):
        """Suggestion suppressed when nothing is close enough — better
        no hint than a misleading one."""
        err = self._run(parser, ["argocd", "totallybogus"], capsys)
        assert "Did you mean" not in err
        assert "invalid choice: 'totallybogus'" in err


class TestSubcommandGroupHelp:
    """Invoking a subcommand group with no subcommand should list
    subcommands, not error with 'Unknown command'."""

    def test_prints_subcommands_for_gitops(self, data, capsys):
        canasta_cli.print_subcommand_help("gitops", data)
        out = capsys.readouterr().out
        assert "Available 'gitops' subcommands:" in out
        assert "init" in out
        assert "fix-submodules" in out
        assert "Run 'canasta gitops <subcommand> --help'" in out

    def test_prints_subcommands_for_config(self, data, capsys):
        canasta_cli.print_subcommand_help("config", data)
        out = capsys.readouterr().out
        assert "regenerate" in out
        assert "Regenerate rendered config" in out

    def test_prints_nested_group_marker_for_backup(self, data, capsys):
        canasta_cli.print_subcommand_help("backup", data)
        out = capsys.readouterr().out
        assert "schedule" in out
        assert "subcommand group" in out


class TestInstallCommand:
    """Test the 'canasta install' command parsing."""

    def test_install_single_package(self, parser):
        args = parser.parse_args(["install", "docker"])
        assert args.command == "install"
        assert args.packages == ["docker"]

    def test_install_multiple_packages(self, parser):
        args = parser.parse_args(["install", "docker", "k3s", "git-crypt"])
        assert args.command == "install"
        assert args.packages == ["docker", "k3s", "git-crypt"]

    def test_install_with_host(self, data):
        from argparse import Namespace
        args = Namespace(
            command="install", host="prod1", verbose=False,
            packages=["docker"],
        )
        result = canasta_cli.build_ansible_args("ap", "install", args, data)
        # install declares --host as a per-command param, so target_host
        # should be set when args.host is provided.
        import json
        for i, arg in enumerate(result):
            if arg == "-e" and i + 1 < len(result) and result[i + 1].startswith("@"):
                with open(result[i + 1][1:]) as f:
                    extra = json.load(f)
                break
        assert extra["target_host"] == "prod1"

    def test_install_resolves_to_correct_command(self, parser):
        args = parser.parse_args(["install", "docker"])
        assert canasta_cli.resolve_command_name(args) == "install"


# ----------------------------------------------------------------
# self_update_cli — pre-exec git pull on the install dir
# ----------------------------------------------------------------

import subprocess as _subprocess


class TestSelfUpdateCli:
    """canasta.self_update_cli runs in canasta.py before exec'ing
    ansible-playbook so the playbook process always sees the post-pull
    state of ansible.cfg / module_utils / etc. (#489)."""

    def _patch_repo(self, monkeypatch, tmp_path, has_git=True, version="4.0.0"):
        """Set up a fake install dir at tmp_path. Returns the dir path."""
        if has_git:
            (tmp_path / ".git").mkdir()
        (tmp_path / "VERSION").write_text(version + "\n")
        monkeypatch.setattr(canasta_cli, "SCRIPT_DIR", str(tmp_path))
        return tmp_path

    def _make_runner(self, side_effects):
        """Return a fake subprocess.run that pops responses from a list.
        Each side_effect is dict(stdout=..., returncode=..., stderr=...).
        """
        calls = []

        def fake_run(argv, **kwargs):
            calls.append(argv)
            if not side_effects:
                raise AssertionError(
                    "unexpected subprocess.run after %d calls: %r"
                    % (len(calls), argv)
                )
            spec = side_effects.pop(0)
            return type("R", (), {
                "returncode": spec.get("returncode", 0),
                "stdout": spec.get("stdout", ""),
                "stderr": spec.get("stderr", ""),
            })()

        return fake_run, calls

    def test_no_op_when_not_a_git_repo(
        self, monkeypatch, tmp_path, capsys,
    ):
        self._patch_repo(monkeypatch, tmp_path, has_git=False)

        def fail_run(*a, **kw):
            raise AssertionError("subprocess.run should not be called")

        monkeypatch.setattr(_subprocess, "run", fail_run)
        canasta_cli.self_update_cli()
        out = capsys.readouterr()
        assert out.out == ""
        assert out.err == ""

    def test_already_up_to_date(self, monkeypatch, tmp_path, capsys):
        self._patch_repo(monkeypatch, tmp_path)
        runner, calls = self._make_runner([
            {"stdout": "abc1234\n"},   # rev-parse current
            {"stdout": ""},             # fetch
            {"stdout": ""},             # log HEAD..origin/main → empty
        ])
        monkeypatch.setattr(_subprocess, "run", runner)
        canasta_cli.self_update_cli()
        out = capsys.readouterr().out
        assert "already up to date" in out
        assert "4.0.0" in out
        assert "abc1234" in out

    def test_pulls_and_updates_build_files(
        self, monkeypatch, tmp_path, capsys,
    ):
        self._patch_repo(monkeypatch, tmp_path, version="4.0.0")
        # Sequence: rev-parse current, fetch, log (has updates),
        # pull, rev-parse new, log -1 date.
        runner, calls = self._make_runner([
            {"stdout": "old1234\n"},
            {"stdout": ""},
            {"stdout": "new1234 some change\n"},   # pending updates
            {"stdout": ""},                         # pull succeeded
            {"stdout": "new1234\n"},
            {"stdout": "2026-05-05 10:00:00\n"},
        ])
        monkeypatch.setattr(_subprocess, "run", runner)

        # VERSION file changes between current and new — the helper
        # re-reads it after the pull.
        version_reads = [iter(["4.0.0", "4.1.0"])]
        original_open = open

        def fake_open(path, *args, **kwargs):
            if str(path).endswith("VERSION") and not args:
                # Only intercept reads of VERSION (no mode arg = read).
                content = next(version_reads[0])
                from io import StringIO
                return StringIO(content)
            return original_open(path, *args, **kwargs)

        monkeypatch.setattr("builtins.open", fake_open)
        canasta_cli.self_update_cli()

        # Restore real open before reading the BUILD files.
        monkeypatch.undo()
        out = capsys.readouterr().out
        assert "Updated Canasta CLI" in out
        assert "4.0.0" in out
        assert "old1234" in out
        assert "4.1.0" in out
        assert "new1234" in out

        assert (tmp_path / "BUILD_COMMIT").read_text().strip() == "new1234"
        assert (tmp_path / "BUILD_DATE").read_text().strip() == (
            "2026-05-05 10:00:00"
        )

    def test_pull_failure_warns_and_continues(
        self, monkeypatch, tmp_path, capsys,
    ):
        self._patch_repo(monkeypatch, tmp_path)
        runner, calls = self._make_runner([
            {"stdout": "abc1234\n"},
            {"stdout": ""},
            {"stdout": "ff00 some change\n"},
            {"stdout": "", "returncode": 1, "stderr": "diverged\n"},
        ])
        monkeypatch.setattr(_subprocess, "run", runner)
        canasta_cli.self_update_cli()
        err = capsys.readouterr().err
        assert "Could not fast-forward" in err

    def test_fetch_failure_warns_and_returns(
        self, monkeypatch, tmp_path, capsys,
    ):
        self._patch_repo(monkeypatch, tmp_path)
        # Sequence: rev-parse current succeeds, fetch raises CalledProcessError.
        attempts = [
            type("R", (), {
                "returncode": 0, "stdout": "abc1234\n", "stderr": "",
            })(),
        ]
        fail_after_first = [False]

        def runner(argv, **kwargs):
            if fail_after_first[0]:
                raise AssertionError("should not run more after fetch fail")
            if "fetch" in argv:
                fail_after_first[0] = True
                check = kwargs.get("check", False)
                if check:
                    raise _subprocess.CalledProcessError(
                        128, argv, output="", stderr="network",
                    )
            return attempts.pop(0)

        monkeypatch.setattr(_subprocess, "run", runner)
        canasta_cli.self_update_cli()
        err = capsys.readouterr().err
        assert "could not fetch from origin" in err
