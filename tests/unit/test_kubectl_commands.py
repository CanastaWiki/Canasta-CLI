"""Repo-wide guard against malformed kubectl command construction.

The crowdsec K8s preflight once passed an unquoted kubectl jsonpath that
contained a space ("name=ready " separator). Ansible's command parser split
the trailing " {end}" into its own token, and kubectl read it as a positional
pod name — failing with "name cannot be provided when a selector is
specified". That broke every K8s crowdsec command.

This is a class of bug (a jsonpath fragment leaking into its own argument)
that string-grep unit tests miss because it only manifests when the command
is actually tokenized. Scan every kubectl command in roles/ and assert each
jsonpath expression survives shlex tokenization as a single argument.
"""

import glob
import os
import re
import shlex

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
_CMD_KEYS = ("ansible.builtin.command", "ansible.builtin.shell", "command", "shell")


def _kubectl_cmds():
    """Yield (file, cmd-string) for every command/shell task cmd in roles/."""
    found = []

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k in _CMD_KEYS and isinstance(v, dict) and isinstance(v.get("cmd"), str):
                    found.append(v["cmd"])
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    out = []
    for f in glob.glob(os.path.join(REPO_ROOT, "roles", "**", "*.yml"), recursive=True):
        try:
            doc = yaml.safe_load(open(f))
        except yaml.YAMLError:
            continue
        found.clear()
        walk(doc)
        for c in found:
            if "kubectl" in c:
                out.append((os.path.relpath(f, REPO_ROOT), c))
    return out


def test_kubectl_jsonpath_is_always_a_single_token():
    """Every `-o jsonpath=...` must tokenize as one argument. A jsonpath with a
    space (e.g. a {range ...} {end} template) must be quoted, or it splits and
    kubectl mistakes the trailing fragment for a resource name."""
    offenders = []
    for f, cmd in _kubectl_cmds():
        if "jsonpath" not in cmd:
            continue
        # {{ jinja }} expressions contain spaces; collapse to a placeholder so
        # tokenization reflects the real argument structure, not the template.
        tokens = shlex.split(re.sub(r"\{\{.*?\}\}", "X", cmd, flags=re.S))
        leaked = [t for t in tokens if t.startswith("{")]
        jsonpath_tokens = [t for t in tokens if "jsonpath" in t]
        if leaked or len(jsonpath_tokens) != 1:
            offenders.append(
                f"{f}: jsonpath split into {jsonpath_tokens!r} with leaked "
                f"fragment(s) {leaked!r} — quote the jsonpath"
            )
    assert not offenders, "Malformed kubectl jsonpath argument(s):\n" + "\n".join(offenders)
