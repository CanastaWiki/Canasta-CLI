"""User-facing messages in folded scalars must not break mid-text.

In a folded (`>`/`>-`) scalar YAML keeps the line break before and after
a more-indented line. Indenting the continuation of a Jinja expression is
harmless while the break falls inside the `{{ }}`, but when the indented
line ends the expression the break lands in the message itself, so
"Saved .env changes to X" and "in the gitops sources." print on separate
lines. This scans every folded `msg`/`fail_msg`/`success_msg` and fails
when its literal text differs from the same block with the indentation
flattened.
"""

import os
import re

import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ROOTS = ["roles", "playbooks", "canasta.yml"]

_KEY = re.compile(r"^(\s*)(- )?(msg|fail_msg|success_msg):\s*(>[-+]?)\s*$")
_TAG = re.compile(r"\{\{.*?\}\}|\{%.*?%\}|\{#.*?#\}", re.S)


def _indent(line):
    return len(line) - len(line.lstrip())


def _literal(header, body):
    value = yaml.safe_load("x: %s\n%s\n" % (header, "\n".join(body)))["x"]
    return _TAG.sub("\0", value or "")


def folded_message_breaks(text):
    """Return the 1-based line numbers of folded messages whose
    more-indented lines put a line break into the message text."""
    lines = text.splitlines()
    bad = []
    for i, line in enumerate(lines):
        m = _KEY.match(line)
        if not m:
            continue
        key_indent = len(m.group(1)) + len(m.group(2) or "")
        body = []
        for nxt in lines[i + 1:]:
            if nxt.strip() and _indent(nxt) <= key_indent:
                break
            body.append(nxt)
        while body and not body[-1].strip():
            body.pop()
        content = [b for b in body if b.strip()]
        if not content:
            continue
        base = _indent(content[0])
        flat = [(" " * base + b.lstrip()) if b.strip() else b for b in body]
        if _literal(m.group(4), body) != _literal(m.group(4), flat):
            bad.append(i + 1)
    return bad


def _yaml_files():
    for root in ROOTS:
        path = os.path.join(REPO_ROOT, root)
        if os.path.isfile(path):
            yield path
            continue
        for dirpath, _, files in os.walk(path):
            for name in files:
                if name.endswith((".yml", ".yaml")):
                    yield os.path.join(dirpath, name)


def test_no_folded_message_breaks_mid_text():
    hits = []
    for path in _yaml_files():
        with open(path) as f:
            text = f.read()
        for lineno in folded_message_breaks(text):
            hits.append("%s:%d" % (os.path.relpath(path, REPO_ROOT), lineno))
    assert not hits, (
        "Folded messages with a more-indented line that ends a Jinja "
        "expression print a stray line break; keep the continuation at "
        "the block's indentation:\n  " + "\n  ".join(hits))


def test_scanner_flags_a_break_outside_the_expression():
    broken = (
        "- debug:\n"
        "    msg: >-\n"
        "      Saved .env changes to\n"
        "      {{ a\n"
        "          + b }}\n"
        "      in the gitops sources.\n"
    )
    assert folded_message_breaks(broken) == [2]


def test_scanner_allows_breaks_inside_the_expression_and_paragraphs():
    ok = (
        "- debug:\n"
        "    msg: >-\n"
        "      Saved .env changes to {{ a\n"
        "          + b\n"
        "      }} in the gitops sources.\n"
        "\n"
        "      A second paragraph.\n"
    )
    assert folded_message_breaks(ok) == []
