"""A Go template containing whitespace must never be passed unquoted.

`{{.Label "com.docker.compose.service"}}` has a space in it. Handed to
`ansible.builtin.shell:` without quoting, /bin/sh splits it into two
words and docker rejects the call with "docker ps accepts no
arguments" — which took out every crowdsec Compose subcommand and
`backup restore`.

Ansible resolves its own `{{ jinja }}` before building the argument
vector, so only text inside `{% raw %}` survives that far; those are
the templates this checks. `command:` with `argv:` avoids the problem
entirely — each element is one argument, whatever it contains.
"""

import os
import re

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

# Files whose tasks query the container runtime with a Go template.
_QUERY_FILES = (
    "roles/crowdsec/tasks/_preflight.yml",
    "roles/orchestrator/tasks/list_running_services.yml",
    "roles/orchestrator/tasks/check_running.yml",
    "roles/orchestrator/tasks/start.yml",
    "roles/orchestrator/tasks/upgrade_rebuild_buildable.yml",
)

_CMD_MODULES = ("ansible.builtin.command", "command")
_RAW_BLOCK = re.compile(
    r"\{%\s*raw\s*%\}(?P<body>.*?)\{%\s*endraw\s*%\}", re.S)


def _read(rel):
    with open(os.path.join(REPO_ROOT, rel)) as f:
        return f.read()


def _tasks(rel):
    """Task dicts from a file, with raw markers stripped so the Go
    templates survive YAML parsing as plain scalars."""
    text = _read(rel).replace("{% raw %}", "").replace("{% endraw %}", "")
    out = []

    def walk(node):
        if isinstance(node, dict):
            out.append(node)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(yaml.safe_load(text))
    return out


def _unprotected_templates():
    """(file, template) for raw Go templates holding whitespace that
    nothing quotes — those split into multiple arguments."""
    hits = []
    for rel in _QUERY_FILES:
        text = _read(rel)
        for m in _RAW_BLOCK.finditer(text):
            body = m.group("body")
            if not re.search(r"\s", body):
                continue                      # nothing to split
            if body[0] in "'\"" and body[-1] == body[0]:
                continue                      # quoted inside the raw block
            line_start = text.rfind("\n", 0, m.start()) + 1
            if re.search(r"['\"]", text[line_start:m.start()]):
                continue                      # quoted by the enclosing scalar
            hits.append((rel, body[:70]))
    return hits


def test_no_unquoted_go_template_contains_whitespace():
    hits = _unprotected_templates()
    assert not hits, (
        "these Go templates contain whitespace but nothing quotes them, so "
        "they are split into separate arguments — use command: with argv:, "
        "or quote them:\n" + "\n".join("  %s\n    %s" % h for h in hits)
    )


def test_the_service_label_queries_use_argv():
    # The two that regressed. Assert the fixed shape directly, so a
    # revert to shell: fails here even if the quoting looks plausible.
    for rel in ("roles/crowdsec/tasks/_preflight.yml",
                "roles/orchestrator/tasks/list_running_services.yml"):
        flat = [
            str(a)
            for task in _tasks(rel)
            for mod in _CMD_MODULES
            if isinstance(task.get(mod), dict) and "argv" in task[mod]
            for a in task[mod]["argv"]
        ]
        assert any("com.docker.compose.service" in a for a in flat), (
            "%s must query the service label through command: argv:, so the "
            "template is never parsed by a shell" % rel
        )
        assert any(a.strip().startswith("{{.Label") for a in flat), (
            "%s: the Go template must be its own argv element" % rel
        )
