"""Tests for scripts/audit_command_coverage.py."""

import os
import sys
import tempfile

import pytest


SCRIPTS_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "scripts"),
)
sys.path.insert(0, SCRIPTS_DIR)

import audit_command_coverage as audit  # noqa: E402


GROUPS = {"backup", "config", "doctor", "extension", "skin", "gitops", "host",
          "maintenance", "sitemap", "devmode", "storage"}


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
            '    inst.run_ok("delete", "-i", "x")\n'
        )
        try:
            results = list(audit.extract_test_invocations(path, GROUPS))
            assert results == [("test_x", "delete")]
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
