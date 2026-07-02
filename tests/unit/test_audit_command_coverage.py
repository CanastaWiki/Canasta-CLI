"""Tests for scripts/audit_command_coverage.py."""

import os
import sys
import tempfile


SCRIPTS_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "scripts"),
)
sys.path.insert(0, SCRIPTS_DIR)

import audit_command_coverage as audit  # noqa: E402


GROUPS = {"backup", "config", "extension", "skin", "gitops", "host",
          "maintenance", "sitemap", "devmode", "storage", "crowdsec"}

# A small stand-in for the command_definitions.yml name set, used to
# exercise longest-path (three-token) resolution.
KNOWN = {"create", "backup_create", "backup_schedule_set",
         "backup_schedule_list", "backup_schedule_remove"}


def write_test_file(content):
    """Write content to a temp .py file and return its path."""
    fd, path = tempfile.mkstemp(suffix=".py", prefix="test_audit_")
    os.close(fd)
    with open(path, "w") as f:
        f.write(content)
    return path


class TestExtractInvocations:
    def test_simple_command(self):
        path = write_test_file(
            'def test_x(inst):\n'
            '    inst.run_ok("create", "-i", "x")\n'
        )
        try:
            results = list(audit.extract_test_invocations(path, GROUPS))
            assert results == [("test_x", "create")]
        finally:
            os.unlink(path)

    def test_subcommand(self):
        path = write_test_file(
            'def test_x(inst):\n'
            '    inst.run_ok("backup", "create", "-i", "x")\n'
        )
        try:
            results = list(audit.extract_test_invocations(path, GROUPS))
            assert results == [("test_x", "backup_create")]
        finally:
            os.unlink(path)

    def test_multiline_call(self):
        path = write_test_file(
            'def test_x(inst):\n'
            '    inst.run_ok(\n'
            '        "create", "-i", "x",\n'
            '        "-w", "main",\n'
            '    )\n'
        )
        try:
            results = list(audit.extract_test_invocations(path, GROUPS))
            assert results == [("test_x", "create")]
        finally:
            os.unlink(path)

    def test_run_quiet_and_run(self):
        path = write_test_file(
            'def test_x(inst):\n'
            '    inst.run_quiet("list")\n'
            '    inst.run("doctor")\n'
            '    inst.run_ok("create", "-i", "x")\n'
        )
        try:
            results = list(audit.extract_test_invocations(path, GROUPS))
            assert sorted(results) == [
                ("test_x", "create"),
                ("test_x", "doctor"),
                ("test_x", "list"),
            ]
        finally:
            os.unlink(path)

    def test_attribution_to_correct_test(self):
        path = write_test_file(
            'def test_a(inst):\n'
            '    inst.run_ok("create", "-i", "x")\n'
            '\n'
            'def test_b(inst):\n'
            '    inst.run_ok("delete", "-i", "x")\n'
        )
        try:
            results = list(audit.extract_test_invocations(path, GROUPS))
            assert sorted(results) == [
                ("test_a", "create"),
                ("test_b", "delete"),
            ]
        finally:
            os.unlink(path)

    def test_non_inst_run_ignored(self):
        path = write_test_file(
            'def test_x(inst):\n'
            '    other.run_ok("create")\n'
            '    subprocess.run_ok("backup", "create")\n'
            '    subprocess.run("backup", "create")\n'
            '    self.run("create")\n'
            '    inst.run_ok("delete", "-i", "x")\n'
        )
        try:
            results = list(audit.extract_test_invocations(path, GROUPS))
            assert results == [("test_x", "delete")]
        finally:
            os.unlink(path)

    def test_secondary_instance_receiver_captured(self):
        # A second test instance (inst_b, inst_a, ...) runs real commands
        # too; the audit must not miss them (it used to only match `inst`).
        path = write_test_file(
            'def test_x(inst):\n'
            '    inst_b.run_ok("create", "-i", "y")\n'
            '    inst_b.run_ok("gitops", "join", "-i", "y")\n'
        )
        try:
            results = list(audit.extract_test_invocations(path, GROUPS))
            assert sorted(results) == [
                ("test_x", "create"),
                ("test_x", "gitops_join"),
            ]
        finally:
            os.unlink(path)

    def test_hyphenated_subcommand_normalized(self):
        # The CLI/tests use the hyphenated user-facing form, but
        # command_definitions.yml names subcommands with underscores.
        path = write_test_file(
            'def test_x(inst):\n'
            '    inst.run_ok("crowdsec", "bouncer-enroll", "-i", "x")\n'
            '    inst.run_ok("gitops", "fix-submodules", "-i", "x")\n'
        )
        try:
            results = list(audit.extract_test_invocations(path, GROUPS))
            assert sorted(results) == [
                ("test_x", "crowdsec_bouncer_enroll"),
                ("test_x", "gitops_fix_submodules"),
            ]
        finally:
            os.unlink(path)

    def test_non_string_first_arg_ignored(self):
        # An interpolated command name (e.g. inst.run_ok(cmd, ...))
        # cannot be statically extracted; the audit silently skips it.
        path = write_test_file(
            'def test_x(inst):\n'
            '    cmd = "create"\n'
            '    inst.run_ok(cmd, "-i", "x")\n'
        )
        try:
            results = list(audit.extract_test_invocations(path, GROUPS))
            assert results == []
        finally:
            os.unlink(path)

    def test_subcommand_group_with_only_first_arg(self):
        # If second arg isn't a string literal, fall back to the group name
        path = write_test_file(
            'def test_x(inst):\n'
            '    sub = "create"\n'
            '    inst.run_ok("backup", sub)\n'
        )
        try:
            results = list(audit.extract_test_invocations(path, GROUPS))
            assert results == [("test_x", "backup")]
        finally:
            os.unlink(path)

    def test_three_token_subcommand(self):
        # A three-token leaf command resolves to group_sub_sub when the
        # known-command set is supplied.
        path = write_test_file(
            'def test_x(inst):\n'
            '    inst.run_ok("backup", "schedule", "set", "-i", "x", "0 3 * * *")\n'
        )
        try:
            results = list(
                audit.extract_test_invocations(path, GROUPS, KNOWN),
            )
            assert results == [("test_x", "backup_schedule_set")]
        finally:
            os.unlink(path)


def _args(*values):
    """Build an ast positional-arg list from literal string values."""
    import ast
    return [ast.Constant(value=v) for v in values]


class TestResolveCommand:
    def test_three_token_resolves_to_group_sub_sub(self):
        args = _args("backup", "schedule", "set", "-i", "x")
        assert audit.resolve_command(args, GROUPS, KNOWN) == \
            "backup_schedule_set"

    def test_two_token_resolves_to_group_sub(self):
        args = _args("backup", "create", "-i", "x")
        assert audit.resolve_command(args, GROUPS, KNOWN) == "backup_create"

    def test_one_token_command(self):
        assert audit.resolve_command(_args("create", "-i", "x"),
                                     GROUPS, KNOWN) == "create"

    def test_trailing_flag_not_folded_into_name(self):
        # The option value after a flag must never join the command name.
        args = _args("backup", "create", "-i", "schedule")
        assert audit.resolve_command(args, GROUPS, KNOWN) == "backup_create"

    def test_leading_tokens_stop_at_first_flag(self):
        args = _args("backup", "create", "--force", "set")
        tokens = audit._leading_name_tokens(args)
        assert tokens == ["backup", "create"]


class TestLoadCommands:
    def test_real_definitions_load(self):
        # Smoke test against the real command_definitions.yml
        names, groups = audit.load_commands()
        assert "create" in names
        assert "backup_create" in names
        assert "backup" in groups
        assert "config" in groups
        # 'create' is a top-level command, not a group
        assert "create" not in groups
