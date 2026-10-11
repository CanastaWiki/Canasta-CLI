"""Escape sequences in Jinja string literals inside `{{ }}`.

Inside a `{{ }}` expression in a folded, literal, plain or single-quoted
scalar, ansible-core 2.21 does not unescape string literals: '\\n' is a
backslash and an "n", so split('\\n') returns the whole input as one
element and join('\\n') joins with a literal backslash-n, and '\\\\s'
reaches a regex as two backslashes. Line splitting uses splitlines(), and
other escapes belong in a double-quoted scalar or a `{% %}` statement.
"""

import base64
import glob
import os
import re

import pytest
import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
EXPR = re.compile(r"\{\{.*?\}\}", re.DOTALL)
LITERAL = re.compile(r"""'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*\"""")
REGEX_CALLS = {"regex_replace", "regex_search", "regex_findall",
               "match", "search", "regex"}
MAPPERS = {"map", "select", "reject", "selectattr", "rejectattr"}
CALL = re.compile(r"(\w+)\s*$")
LEADING_ARG = re.compile(r"""\s*(?:'(\w+)'|"(\w+)"|\w+)\s*,""")


def _strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for v in node.values():
            yield from _strings(v)
    elif isinstance(node, list):
        for v in node:
            yield from _strings(v)


def _find(node, key):
    if isinstance(node, dict):
        if key in node:
            return node[key]
        node = list(node.values())
    if isinstance(node, list):
        for v in node:
            found = _find(v, key)
            if found is not None:
                return found
    return None


def _yaml_files():
    yield os.path.join(REPO_ROOT, "canasta.yml")
    for sub in ("roles", "playbooks"):
        yield from glob.glob(os.path.join(REPO_ROOT, sub, "**", "*.yml"),
                             recursive=True)


def _leading_names(args):
    """The quoted names among a call's first two arguments.

    A mapper names the test or filter it applies in its first argument, or
    in its second after selectattr's attribute.
    """
    names, pos = set(), 0
    for _ in range(2):
        m = LEADING_ARG.match(args, pos)
        if m is None:
            break
        names.add(m.group(1) or m.group(2))
        pos = m.end()
    return names


def _regex_argument(expr, start):
    """Whether the literal at expr[start] feeds an enclosing regex call.

    A raw single-backslash escape is what a regex wants, so such a literal
    is unaffected by the missing unescape.
    """
    masked = LITERAL.sub(lambda m: "'" + "x" * (len(m.group(0)) - 2) + "'",
                         expr[:start])
    depth = 0
    for i in range(len(masked) - 1, -1, -1):
        if masked[i] == ")":
            depth += 1
        elif masked[i] == "(" and depth > 0:
            depth -= 1
        elif masked[i] == "(":
            call = CALL.search(masked[:i])
            name = call.group(1) if call else ""
            if name in REGEX_CALLS or (
                    name in MAPPERS
                    and _leading_names(expr[i + 1:start]) & REGEX_CALLS):
                return True
    return False


def _escape_offenders(text):
    for expr in EXPR.findall(text):
        for lit in LITERAL.finditer(expr):
            body = lit.group(0)[1:-1]
            if "\\" not in body:
                continue
            if "\\\\" in body or not _regex_argument(expr, lit.start()):
                yield lit.group(0)


@pytest.mark.parametrize("text, flagged", [
    ("{{ x | join('\\n') }}", ["'\\n'"]),
    ("{{ x.split('\\n') }}", ["'\\n'"]),
    ("{{ 'a' ~ '\\t' ~ 'b' }}", ["'\\t'"]),
    ("{{ x | regex_replace('\\\\s+', ' ') }}", ["'\\\\s+'"]),
    ("{{ x | map('regex_replace', '^\\\\./', '') }}", ["'^\\\\./'"]),
    ("{{ x | regex_replace('\\s+', ' ') }}", []),
    ("{{ x | regex_replace('(a)', '\\1') }}", []),
    ("{{ x | map('regex_replace', '^(.*)$', k ~ \" '\\1' x\") }}", []),
    ("{{ x | select('match', '^a\\t') | map('regex_replace', '^[^\\t]*\\t', '') }}",
     []),
    ("{{ x | selectattr('value', 'search', '\\s') }}", []),
    ("{{ x is match('a\\.b') }}", []),
    ("{{ x | regex_search('(f(o)o)\\.', '\\1') }}", []),
    ("{{ x | map('regex_replace', 'a', b | ternary('\\1', 'c')) }}", []),
    ("{{ x | map('upper') | join('\\t') }}", ["'\\t'"]),
    ("{{ (x | regex_search('a\\.b')) ~ '\\n' }}", ["'\\n'"]),
    ("{{ x | join('\n') }}", []),
    ("{% set nl = '\\n' %}{{ x | join(nl) }}", []),
])
def test_escape_scanner(text, flagged):
    assert list(_escape_offenders(text)) == flagged


def test_no_escaped_literals_in_expressions():
    offenders = []
    for path in _yaml_files():
        with open(path) as f:
            doc = yaml.safe_load(f)
        for s in _strings(doc):
            offenders += ["%s: %s" % (os.path.relpath(path, REPO_ROOT), lit)
                          for lit in _escape_offenders(s)]
    assert not offenders, sorted(set(offenders))


def _b64(s):
    return base64.b64encode(s.encode()).decode()


@pytest.mark.parametrize("path, key, variables, expected", [
    ("roles/gitops/tasks/_apply_argo_repo_secret.yml", "_existing",
     {"_argo_known_hosts": {"resources": [
         {"data": {"ssh_known_hosts": "h1 k1\nh2 k2\n"}}]}},
     ["h1 k1", "h2 k2"]),
    ("roles/gitops/tasks/_render_mycnf.yml", "_mycnf_missing",
     {"_mycnf_missing_raw": "A\nB\n"}, ["A", "B"]),
    ("roles/gitops/tasks/_migrate_reclassified_secrets.yml", "_rc_lines",
     {"_rc_template": {"content": _b64("FOO_PASSWORD=x\nBAR_SECRET=y\n")},
      "canasta_secret_key_regex": ".*"},
     ["FOO_PASSWORD=x", "BAR_SECRET=y"]),
    ("roles/orchestrator/tasks/k8s_capture_rollout_failure.yml", "msg",
     {"_rollout_state": {"stdout": "l1\nl2"}},
     ["The rollout did not finish. What the cluster reports:", "",
      "l1", "l2"]),
    ("roles/orchestrator/tasks/k8s_sync_config.yml", "_app_secret_env",
     {"_app_secrets_web_raw": {"content": _b64("K1\nK2\n")},
      "_app_secrets_web_stat": {"stat": {"exists": True}}},
     ["K1", "K2"]),
])
def test_expression_splits_into_lines(path, key, variables, expected):
    with open(os.path.join(REPO_ROOT, path)) as f:
        expr = _find(yaml.safe_load(f), key)
    rendered = Templar(loader=DataLoader(), variables=variables).template(
        trust_as_template(expr))
    assert rendered == expected
