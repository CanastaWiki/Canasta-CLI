"""No executed command may hardcode the container runtime.

compose_command / inspect_command exist so an instance can run under a
different runtime. A single missed call site silently reverts to Docker
for that one operation, which stays invisible until someone runs that
command on a Podman host — and the Python fast path and the Ansible
roles can drift apart from each other without anyone noticing.

Only *executed* values are checked: Ansible `cmd:` / `argv:` / `*_cmd`
facts, and command lists in Python. Task names, `msg:` text, comments
and docstrings are prose and may say "docker compose" freely.
"""

import os
import re

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

_SCANNED = ("canasta.py", "direct_commands", "playbooks", "roles")

_SUBCOMMANDS = (r"compose\b|ps\b|inspect\b|volume\b|run\b|pull\b|rm\b|"
                r"cp\b|exec\b|image\b|build\b|tag\b|push\b|create\b|"
                r"start\b|login\b")

# Strict: anchored so prose ("before `docker compose up`") does not match.
# Values reaching it are whole command strings, so ^ is the common case.
_RUNTIME = re.compile(
    r"(?:^|[;&|]\s*|\$\(\s*)docker\s+"
    r"(?:" + _SUBCOMMANDS + r")")

# Loose: only for the staleness check, which asks whether an allowlisted
# file still names a runtime anywhere — quoting and indentation vary.
_MENTIONS = re.compile(r"\bdocker\s+(?:" + _SUBCOMMANDS + r")")
_PY_LIST = re.compile(r"""\[\s*["']docker["']\s*,""")
# A shell command in a string literal: the quote is immediately
# followed by the runtime, which prose never is.
_PY_CMD = re.compile(r"""["']docker\s+(?:""" + _SUBCOMMANDS + r""")""")

# Keys whose values are executed.
_CMD_KEYS = re.compile(r"(?:^|_)(?:cmd|argv)$")

# path -> why this file legitimately names Docker.
ALLOW = {
    "roles/install/tasks/docker.yml":
        "installs and verifies Docker itself",
    "direct_commands/doctor.py":
        "probes for Docker by name and reports the result",
    "direct_commands/_helpers.py":
        "defines the ['docker', 'compose'] default the overrides fall back to",
}


def _templated(text):
    return "compose_command" in text or "inspect_command" in text


def _walk_yaml(node, hits, rel, key=None):
    if isinstance(node, dict):
        for k, v in node.items():
            _walk_yaml(v, hits, rel, k)
    elif isinstance(node, list):
        for item in node:
            _walk_yaml(item, hits, rel, key)
    elif isinstance(node, str):
        if key and _CMD_KEYS.search(str(key)) and not _templated(node):
            if _RUNTIME.search(node):
                hits.append((rel, str(key), node.strip().split("\n")[0][:80]))


def _scan_yaml(rel, full):
    hits = []
    try:
        with open(full) as f:
            docs = list(yaml.safe_load_all(f))
    except Exception:
        return hits
    for doc in docs:
        _walk_yaml(doc, hits, rel)
    return hits


def _scan_python(rel, full):
    hits = []
    with open(full) as f:
        for lineno, line in enumerate(f, 1):
            stripped = line.strip()
            if stripped.startswith("#") or _templated(line):
                continue
            # Message text, not an executed command.
            if "print(" in line or "Error:" in line:
                continue
            if (_PY_LIST.search(line) or _PY_CMD.search(line)
                    or _RUNTIME.search(line)):
                hits.append((rel, str(lineno), stripped[:80]))
    return hits


def _iter_source():
    for entry in _SCANNED:
        target = os.path.join(REPO_ROOT, entry)
        if os.path.isfile(target):
            yield os.path.relpath(target, REPO_ROOT), target
            continue
        for root, _dirs, files in os.walk(target):
            for name in sorted(files):
                if name.endswith((".py", ".yml", ".yaml")):
                    full = os.path.join(root, name)
                    yield os.path.relpath(full, REPO_ROOT), full


def _hardcoded_sites():
    hits = []
    for rel, full in _iter_source():
        if rel in ALLOW:
            continue
        if rel.endswith((".yml", ".yaml")):
            hits += _scan_yaml(rel, full)
        else:
            hits += _scan_python(rel, full)
    return hits


def test_no_executed_command_hardcodes_the_runtime():
    hits = _hardcoded_sites()
    assert not hits, (
        "these executed commands hardcode the container runtime — use "
        "compose_command / inspect_command, or add the file to ALLOW with "
        "a reason:\n" + "\n".join("  %s [%s]  %s" % h for h in hits)
    )


def test_allowlist_entries_are_not_stale():
    for rel in ALLOW:
        full = os.path.join(REPO_ROOT, rel)
        assert os.path.exists(full), "ALLOW names a missing file: %s" % rel
        with open(full) as f:
            content = f.read()
        assert _MENTIONS.search(content) or _PY_LIST.search(content), (
            "%s is allowlisted but no longer names a runtime — drop the "
            "ALLOW entry" % rel
        )
