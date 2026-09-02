"""No task may reference a result field as a bare name in a conditional.

ansible-core does not expose a task result's fields as bare names when it
evaluates `changed_when` / `failed_when`. The result dict still carries
`stdout`, but `"'Untagged' in stdout"` raises "'stdout' is undefined" — and
a conditional that fails to evaluate fails the task even under
`failed_when: false`, so the play aborts partway through its work.

`canasta image prune` removed images and then died on exactly this, leaving
the Kubernetes half of the reclaim unrun.
"""

import os
import re

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
SEARCH_DIRS = ("roles", "playbooks")
CONDITIONALS = ("changed_when", "failed_when")

# Result fields that only exist on the task result, never as play vars.
RESULT_FIELDS = (
    "stdout", "stderr", "rc", "stdout_lines", "stderr_lines",
)
# A bare reference: the name not preceded by a dot (foo.stdout) and not part
# of a longer identifier (my_stdout).
BARE = re.compile(
    r"(?<![.\w])(%s)(?![\w])" % "|".join(RESULT_FIELDS)
)


def _yaml_files():
    for base in SEARCH_DIRS:
        for root, _, files in os.walk(os.path.join(REPO_ROOT, base)):
            for name in files:
                if name.endswith((".yml", ".yaml")):
                    yield os.path.join(root, name)


def _walk(node):
    """Every task-shaped dict in a loaded playbook or task file."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def test_no_bare_result_field_in_conditionals():
    offenders = []
    for path in _yaml_files():
        try:
            with open(path) as f:
                doc = yaml.safe_load(f)
        except yaml.YAMLError:
            continue  # templates that are not valid YAML on their own
        for task in _walk(doc):
            for key in CONDITIONALS:
                expr = task.get(key)
                if not isinstance(expr, str):
                    continue
                match = BARE.search(expr)
                if match:
                    offenders.append(
                        "%s: %s: %s (bare '%s')" % (
                            os.path.relpath(path, REPO_ROOT),
                            task.get("name", "<unnamed>"),
                            expr, match.group(1),
                        )
                    )
    assert not offenders, (
        "these conditionals reference a result field as a bare name, which "
        "ansible-core cannot resolve; use a registered variable or drop the "
        "conditional:\n  %s" % "\n  ".join(offenders)
    )
