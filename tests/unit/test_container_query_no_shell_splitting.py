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
        in_argv = [
            str(a)
            for task in _tasks(rel)
            for mod in _CMD_MODULES
            if isinstance(task.get(mod), dict) and "argv" in task[mod]
            for a in task[mod]["argv"]
        ]
        for m in _RAW_BLOCK.finditer(text):
            body = m.group("body")
            if not re.search(r"\s", body):
                continue                      # nothing to split
            if any(body in a for a in in_argv):
                continue                      # one argv element, no shell
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
        assert any("{{.Label" in a and a.strip().startswith("{%")
                   or a.strip().startswith("{{.Label") for a in flat), (
            "%s: the Go template must be its own argv element" % rel
        )


_SERVICE_LABEL_FILES = {
    "roles/crowdsec/tasks/_preflight.yml": "inspect_command",
    "roles/orchestrator/tasks/list_running_services.yml": "inspect_command",
    "roles/orchestrator/tasks/start.yml": "_inspect_cmd",
}


def _service_format_scalars(rel):
    """The unrendered argv scalars that build the service-label format."""
    raw = yaml.safe_load(_read(rel))
    out = []

    def walk(node):
        if isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, str) and "com.docker.compose.service\"}}" in node:
            out.append(node)

    walk(raw)
    return out


def test_service_label_format_matches_the_runtime():
    # Podman 4 has no .Label template method; Docker's .Labels is a
    # string that index cannot read. Each runtime needs its own form.
    import jinja2

    env = jinja2.Environment()
    env.filters["basename"] = os.path.basename
    for rel, var in _SERVICE_LABEL_FILES.items():
        scalars = _service_format_scalars(rel)
        assert scalars, "%s: no service-label format found" % rel
        for scalar in scalars:
            for runtime, want in (
                    ("podman", '{{index .Labels "com.docker.compose.service"}}'),
                    ("/usr/bin/podman",
                     '{{index .Labels "com.docker.compose.service"}}'),
                    ("docker", '{{.Label "com.docker.compose.service"}}')):
                got = env.from_string(scalar).render({var: runtime}).strip()
                assert got == want, (rel, runtime, got)


def test_direct_commands_service_label_format_matches_the_runtime():
    from direct_commands._helpers import _PS_SERVICE_FORMAT

    assert _PS_SERVICE_FORMAT["podman"] == (
        '{{index .Labels "com.docker.compose.service"}}')
    assert _PS_SERVICE_FORMAT["docker"] == (
        '{{.Label "com.docker.compose.service"}}')
