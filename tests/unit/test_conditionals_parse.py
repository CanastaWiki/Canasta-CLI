"""Every conditional must parse as a Jinja expression.

A line indented one level too deep under a conditional is folded into it by
YAML: `changed_when: true` followed by a stray `-C 'x'` line becomes the
string "true -C 'x'", which fails at run time. Parsing each conditional
catches that class of mistake before it ships.
"""

import os

import jinja2
import pytest
import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
SEARCH_DIRS = ("roles", "playbooks")
CONDITIONALS = ("when", "changed_when", "failed_when", "until")
ENV = jinja2.Environment()


def _yaml_files():
    for base in SEARCH_DIRS:
        for root, _, files in os.walk(os.path.join(REPO_ROOT, base)):
            for name in files:
                if name.endswith((".yml", ".yaml")):
                    yield os.path.join(root, name)


def _tasks(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _tasks(value)
    elif isinstance(node, list):
        for item in node:
            yield from _tasks(item)


def _expressions(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, str):
                yield item


def _conditionals():
    for path in _yaml_files():
        try:
            with open(path) as f:
                doc = yaml.safe_load(f)
        except yaml.YAMLError:
            continue
        for task in _tasks(doc):
            for key in CONDITIONALS:
                for expr in _expressions(task.get(key)):
                    yield os.path.relpath(path, REPO_ROOT), key, expr


def _parses(expr):
    text = expr.strip()
    if not (text.startswith("{{") and text.endswith("}}")):
        text = "{{ %s }}" % text
    try:
        ENV.parse(text)
        return True
    except jinja2.TemplateSyntaxError:
        return False


def test_every_conditional_parses():
    bad = ["%s: %s: %s" % (path, key, expr)
           for path, key, expr in _conditionals() if not _parses(expr)]
    assert not bad, "Conditionals that are not valid expressions:\n  " + "\n  ".join(bad)


def test_the_guard_sees_conditionals():
    assert sum(1 for _ in _conditionals()) > 500


@pytest.mark.parametrize("expr, ok", [
    ("true", True),
    ("x is changed", True),
    ("true -C 'canasta-x'", False),
])
def test_parser(expr, ok):
    assert _parses(expr) is ok
