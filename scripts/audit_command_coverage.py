#!/usr/bin/env python3
"""Static integration-test coverage report for the canasta CLI.

Walks tests/integration/run_tests.py with Python's ast module, finds every
inst.run / inst.run_ok / inst.run_quiet call, and extracts the command name
(or `<group> <subcommand>` pair) from the first one or two string arguments.
Cross-references against meta/command_definitions.yml to report:

  * which commands have at least one integration test that exercises them
  * which commands are not exercised by any integration test
  * which test functions exercise each command

Static analysis only — does not run the tests, does not need a Docker
daemon. Catches everything that's a literal string in a `run` call. Misses
commands invoked via interpolated names (none today). Does not measure
flag-level coverage; for that, read the test source.

Usage:
    python scripts/audit_command_coverage.py
    python scripts/audit_command_coverage.py --strict   # exit 1 if any
                                                          # command is
                                                          # uncovered
"""

from __future__ import annotations

import argparse
import ast
import os
import sys
from collections import defaultdict

import yaml


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
TEST_FILE = os.path.join(REPO_ROOT, "tests", "integration", "run_tests.py")
DEFS_FILE = os.path.join(REPO_ROOT, "meta", "command_definitions.yml")

RUN_METHODS = {"run", "run_ok", "run_quiet"}


def load_commands():
    """Return (all_command_names, subcommand_groups).

    all_command_names is the canonical set from command_definitions.yml,
    using underscore separation for subcommands (e.g. "backup_create").
    subcommand_groups is the set of first-segment names that have at
    least one underscore subcommand (e.g. "backup", "config").
    """
    with open(DEFS_FILE) as f:
        defs = yaml.safe_load(f)
    names = {c["name"] for c in defs.get("commands", [])}
    groups = {n.split("_", 1)[0] for n in names if "_" in n}
    return names, groups


def _string_const(node):
    """Return the value of an ast string Constant, or None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def extract_test_invocations(test_file, subcommand_groups):
    """Walk the test file and yield (test_func_name, command_name) tuples.

    command_name is the canonical underscore-joined form
    (e.g. "create", "backup_create"). Calls whose first argument is not
    a string literal are ignored — there are none in run_tests.py today,
    but if any are added the audit will silently miss them.
    """
    with open(test_file) as f:
        tree = ast.parse(f.read(), filename=test_file)

    # Map line number ranges -> enclosing test function name so each
    # extracted invocation can be attributed to its test.
    func_ranges = []  # list of (start_line, end_line, name)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
            func_ranges.append(
                (node.lineno, getattr(node, "end_lineno", node.lineno), node.name),
            )

    def func_for_line(lineno):
        for start, end, name in func_ranges:
            if start <= lineno <= end:
                return name
        return None

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr in RUN_METHODS):
            continue
        # Filter to inst.run* calls (the test instance method).
        if not (isinstance(func.value, ast.Name) and func.value.id == "inst"):
            continue

        # First positional arg is the command name (or group name).
        if not node.args:
            continue
        first = _string_const(node.args[0])
        if first is None:
            continue

        # If the first arg is a subcommand group, the second positional
        # arg is the actual subcommand name.
        if first in subcommand_groups and len(node.args) >= 2:
            second = _string_const(node.args[1])
            if second is not None:
                command = "%s_%s" % (first, second)
            else:
                command = first
        else:
            command = first

        yield func_for_line(node.lineno), command


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit with status 1 if any command is uncovered.",
    )
    parser.add_argument(
        "--show-tests",
        action="store_true",
        help="For each covered command, list the test functions exercising it.",
    )
    args = parser.parse_args()

    all_commands, groups = load_commands()
    coverage = defaultdict(set)  # command -> {test_func_name, ...}
    unknown = set()              # invoked commands not in definitions

    for test_name, command in extract_test_invocations(TEST_FILE, groups):
        if command in all_commands:
            coverage[command].add(test_name or "?")
        else:
            unknown.add(command)

    covered = sorted(c for c in all_commands if c in coverage)
    uncovered = sorted(c for c in all_commands if c not in coverage)

    total = len(all_commands)
    n_covered = len(covered)
    pct = (n_covered * 100.0 / total) if total else 0.0

    print("Integration test coverage: %d / %d commands (%.1f%%)" % (n_covered, total, pct))
    print("=" * 60)
    print()

    print("Covered (%d):" % n_covered)
    for cmd in covered:
        if args.show_tests:
            tests = sorted(coverage[cmd])
            print("  %-32s %s" % (cmd, ", ".join(tests)))
        else:
            print("  %s" % cmd)
    print()

    print("NOT covered (%d):" % len(uncovered))
    for cmd in uncovered:
        print("  %s" % cmd)
    print()

    if unknown:
        print("WARNING: %d invocations matched no known command:" % len(unknown))
        for cmd in sorted(unknown):
            print("  %s" % cmd)
        print()

    if args.strict and uncovered:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
