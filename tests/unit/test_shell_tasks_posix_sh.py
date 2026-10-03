"""Shell tasks must not use bash-only syntax unless they run under bash.

`ansible.builtin.shell` runs /bin/sh, which is dash on Debian and Ubuntu.
dash rejects a here-string (`<<<`) or process substitution (`<(`) with a
syntax error and has no `[[` builtin, so a task that works on a macOS
controller (where /bin/sh is bash) fails on a Debian target. A task that
needs these sets `executable: /bin/bash`.
"""

import os
import re

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
SEARCH_DIRS = ("roles", "playbooks")
SHELL_MODULES = ("ansible.builtin.shell", "shell")

BASH_ONLY = {
    "here-string <<<": re.compile(r"<<<"),
    "process substitution <(": re.compile(r"<\("),
    "[[ test": re.compile(r"(^|[\s;&|(])\[\[\s"),
    "set -o pipefail": re.compile(r"set\s+-\w*o\s+pipefail"),
}


def _yaml_files():
    for base in SEARCH_DIRS:
        for root, _, files in os.walk(os.path.join(REPO_ROOT, base)):
            for name in files:
                if name.endswith((".yml", ".yaml")):
                    yield os.path.join(root, name)


def _dicts(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _dicts(value)
    elif isinstance(node, list):
        for item in node:
            yield from _dicts(item)


def _shell_tasks():
    for path in _yaml_files():
        try:
            with open(path) as f:
                doc = yaml.safe_load(f)
        except yaml.YAMLError:
            continue
        for task in _dicts(doc):
            for module in SHELL_MODULES:
                if module not in task:
                    continue
                spec = task[module]
                args = task.get("args") or {}
                if isinstance(spec, dict):
                    cmd = spec.get("cmd") or spec.get("_raw_params") or ""
                    executable = spec.get("executable") or args.get("executable")
                else:
                    cmd = spec or ""
                    executable = args.get("executable")
                yield (os.path.relpath(path, REPO_ROOT), task.get("name", "?"),
                       str(cmd), executable)


def _violations(cmd, executable):
    if executable and os.path.basename(str(executable)) == "bash":
        return []
    return [label for label, pattern in BASH_ONLY.items()
            if pattern.search(cmd)]


def test_no_bash_only_syntax_under_sh():
    bad = []
    for path, name, cmd, executable in _shell_tasks():
        for label in _violations(cmd, executable):
            bad.append("%s: %s: %s" % (path, name, label))
    assert not bad, (
        "Shell tasks using bash-only syntax without executable: /bin/bash:\n  "
        + "\n  ".join(bad))


def test_the_guard_sees_shell_tasks():
    assert sum(1 for _ in _shell_tasks()) > 20


def test_guard_flags_each_construct():
    for cmd in ("python3 -c 'x' <<< \"$PW\"",
                "diff <(sort a) <(sort b)",
                "if [[ -f x ]]; then :; fi",
                "set -o pipefail; a | b",
                "set -eo pipefail; a | b"):
        assert _violations(cmd, None), cmd


def test_guard_allows_bash_and_posix():
    assert not _violations("a <<< b", "/bin/bash")
    for cmd in ("grep -E '^[[:space:]]*x' f",
                "cat <<'EOF'\nx\nEOF",
                "[ -f x ] && echo y",
                "a | b"):
        assert not _violations(cmd, None), cmd
