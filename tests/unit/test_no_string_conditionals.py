"""Conditionals must evaluate to a boolean.

ansible-core 2.19+ fails a task whose `when`, `changed_when`,
`failed_when` or `until` evaluates to a string. The common way to get
one is testing an optional string variable for presence with
`var | default('')`: it works on older ansible-core and breaks
at runtime on current versions, which the unit tests and ansible-lint
do not exercise. Use `var | default('') | length > 0` instead.
"""

import glob
import os
import re

import pytest
import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
CONDITION_KEYS = ("when", "changed_when", "failed_when", "until")
STRING_DEFAULT = re.compile(
    r"""^\s*\(?\s*[A-Za-z_][\w.]*(\[[^\]]*\])*\s*\|\s*default\(\s*(''|"")\s*\)\s*\)?\s*$"""
)


def _yaml_files():
    patterns = (
        "roles/*/tasks/**/*.yml",
        "roles/*/handlers/**/*.yml",
        "playbooks/**/*.yml",
        "*.yml",
    )
    for pattern in patterns:
        yield from glob.glob(os.path.join(REPO_ROOT, pattern), recursive=True)


def _conditions(node):
    if isinstance(node, dict):
        for key, value in node.items():
            if key in CONDITION_KEYS:
                for cond in value if isinstance(value, list) else [value]:
                    if isinstance(cond, str):
                        yield key, cond
            else:
                yield from _conditions(value)
    elif isinstance(node, list):
        for item in node:
            yield from _conditions(item)


def _string_conditionals():
    found = []
    for path in _yaml_files():
        with open(path) as f:
            data = yaml.safe_load(f)
        for key, cond in _conditions(data):
            if STRING_DEFAULT.match(cond):
                found.append(f"{os.path.relpath(path, REPO_ROOT)}: {key}: {cond}")
    return found


def test_no_string_valued_conditionals():
    assert _string_conditionals() == []


@pytest.mark.parametrize(
    "cond",
    ["x | default('')", 'x | default("")', "a.b | default('')",
     "(x | default(''))", "x['k'] | default('')"],
)
def test_pattern_flags_string_defaults(cond):
    assert STRING_DEFAULT.match(cond)


@pytest.mark.parametrize(
    "cond",
    ["x | default('') | length > 0", "not x | default('')",
     "x | default(false)", "x | default('') == 'gpg'", "x is defined",
     "x | default('') | bool"],
)
def test_pattern_allows_boolean_expressions(cond):
    assert not STRING_DEFAULT.match(cond)
