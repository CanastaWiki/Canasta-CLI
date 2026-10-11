"""Code comments must state the reason, not cite a GitHub issue number.

A bare issue number in a comment or docstring tells the reader nothing without
a trip to GitHub, and the reference rots when issues move. This scans the
comments and docstrings of the code trees for issue/PR citations. A
reference to a third-party repository (``owner/repo#N``) is allowed: it
points at an upstream bug outside this project. String literals that are
not docstrings (test fixture data, assertion messages, user-facing text)
are not comments and are not scanned.
"""

import ast
import io
import os
import re
import tokenize

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TREES = [
    "roles", "playbooks", "direct_commands", "filter_plugins",
    "callback_plugins", "scripts", "tests", ".github/workflows",
]
FILES = [
    "canasta.py", "canasta.yml", "canasta-native", "canasta-docker",
    "get-canasta.sh", "Dockerfile", "Makefile",
]
SHELL_LIKE = (".yml", ".yaml", ".sh", ".j2", ".tpl", ".cfg", ".toml")

# `#` plus digits, not glued to a word or path (so `owner/repo#1234` and hex-ish
# tokens like `abc#12` don't match), or any CanastaWiki repository ref.
_ISSUE_REF = re.compile(
    r"(?<![\w&#])#\d{2,6}\b"
    r"|CanastaWiki/[\w.-]+#\d+"
    r"|github\.com/CanastaWiki/[\w.-]+/(?:issues|pull)/\d+"
)


def _python_comments(source):
    """Yield (line, text) for every comment and docstring in Python source."""
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, SyntaxError):
        return
    for tok in tokens:
        if tok.type == tokenize.COMMENT:
            yield tok.start[0], tok.string
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = node.body
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                yield body[0].lineno, body[0].value.value


def _hash_comment(line):
    """Return the `#` comment on a YAML/shell line, or None.

    A `#` starts a comment at the start of the line or after whitespace,
    outside single or double quotes.
    """
    quote = None
    prev = " "
    for i, ch in enumerate(line):
        if quote:
            if ch == quote and prev != "\\":
                quote = None
        elif ch in ("'", '"'):
            quote = ch
        elif ch == "#" and prev in (" ", "\t"):
            return line[i:]
        prev = ch
    return None


def _shell_like_comments(source):
    """Yield (line, text) for `#`, Jinja `{# #}` and `/* */` comments."""
    for lineno, line in enumerate(source.splitlines(), 1):
        comment = _hash_comment(line)
        if comment:
            yield lineno, comment
    for pattern in (r"\{#.*?#\}", r"/\*.*?\*/"):
        for m in re.finditer(pattern, source, re.S):
            yield source.count("\n", 0, m.start()) + 1, m.group(0)


def _is_python(path):
    if path.endswith(".py"):
        return True
    if os.path.splitext(path)[1]:
        return False
    with open(path, encoding="utf-8", errors="replace") as fh:
        first = fh.readline()
    return first.startswith("#!") and "python" in first


def _candidate_files():
    for name in FILES:
        path = os.path.join(REPO, name)
        if os.path.isfile(path):
            yield path
    for tree in TREES:
        for root, dirs, files in os.walk(os.path.join(REPO, tree)):
            dirs[:] = [d for d in dirs if d not in ("__pycache__", ".venv")]
            for name in files:
                path = os.path.join(root, name)
                if (name.endswith(".py") or name.endswith(SHELL_LIKE)
                        or "." not in name):
                    yield path


def _comments(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        source = fh.read()
    if _is_python(path):
        return _python_comments(source)
    return _shell_like_comments(source)


def find_issue_refs():
    hits = []
    for path in _candidate_files():
        for lineno, text in _comments(path):
            for m in _ISSUE_REF.finditer(text):
                rel = os.path.relpath(path, REPO)
                hits.append("%s:%d: %s" % (rel, lineno, m.group(0)))
    return hits


def test_no_issue_numbers_in_code_comments():
    hits = find_issue_refs()
    assert not hits, (
        "Code comments and docstrings cite GitHub issues. Replace each "
        "citation with the reason it stands for:\n  " + "\n  ".join(hits))


def test_scanner_flags_comment_citations():
    py = '"""Doc (#12)."""\nx = "#34"  # see #56\n'
    found = [t for _, t in _python_comments(py)]
    assert any("#12" in t for t in found)
    assert any("#56" in t for t in found)
    assert not any("#34" in t for t in found)

    yml = 'msg: "Step #78"\n# fixed in CanastaWiki/Canasta-CLI#90\nx: 1  # #11\n'
    found = [t for _, t in _shell_like_comments(yml)]
    assert [m.group(0) for t in found for m in _ISSUE_REF.finditer(t)] == [
        "CanastaWiki/Canasta-CLI#90", "#11"]

    assert not _ISSUE_REF.search("# containers/podman-compose#1105")
    assert not _ISSUE_REF.search("# color: #fff")
