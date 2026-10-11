"""Read a gitops init or join flow with its shared steps inlined.

init and join include steps they share (_join_clone_repo.yml,
_init_commit_push.yml, ...). Structural tests that assert what a flow does
and in which order read the flow through these helpers, so they see each
shared step where it runs. An include's literal vars are substituted for
their {{ name }} references, so a step reads as it does for that caller.
"""

import os
import re

import yaml

GITOPS_TASKS = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "roles", "gitops", "tasks"))

SHARED_FLOW_FILES = frozenset({
    "_git_identity.yml",
    "_init_commit_push.yml",
    "_init_convert_submodules.yml",
    "_init_require_no_repo.yml",
    "_join_checkout_differing.yml",
    "_join_clone_repo.yml",
    "_join_commit_push.yml",
    "_join_move_git.yml",
    "_join_read_hosts.yml",
    "_join_require_no_repo.yml",
    "_register_host.yml",
})


def _shared_include(task):
    inc = task.get("ansible.builtin.include_tasks")
    return inc if isinstance(inc, str) and inc in SHARED_FLOW_FILES else None


def _substitute(node, values):
    if isinstance(node, str):
        for name, value in values.items():
            node = re.sub(r"\{\{\s*%s\s*\}\}" % re.escape(name),
                          lambda _m, v=value: v, node)
        return node
    if isinstance(node, list):
        return [_substitute(n, values) for n in node]
    if isinstance(node, dict):
        return {k: _substitute(v, values) for k, v in node.items()}
    return node


def _load(name):
    with open(os.path.join(GITOPS_TASKS, name)) as f:
        return yaml.safe_load(f) or []


def _expand(tasks):
    out = []
    for task in tasks or []:
        if not isinstance(task, dict):
            out.append(task)
            continue
        inc = _shared_include(task)
        if inc:
            values = {k: str(v) for k, v in (task.get("vars") or {}).items()
                      if isinstance(v, (str, int))}
            out.extend(_expand(_substitute(_load(inc), values)))
            continue
        task = dict(task)
        for key in ("block", "rescue", "always"):
            if key in task:
                task[key] = _expand(task[key])
        out.append(task)
    return out


def flow_tasks(name):
    """The tasks of gitops/tasks/<name>, shared steps inlined."""
    return _expand(_load(name))


def flow_source(name):
    """The text of gitops/tasks/<name> followed by each shared step it
    includes, for assertions about what the flow's source says."""
    seen = []

    def collect(fname):
        for task in _walk(_load(fname)):
            inc = _shared_include(task)
            if inc and inc not in seen:
                seen.append(inc)
                collect(inc)

    collect(name)
    parts = []
    for fname in [name] + seen:
        with open(os.path.join(GITOPS_TASKS, fname)) as f:
            parts.append(f.read())
    return "\n".join(parts)


def _walk(tasks):
    for task in tasks or []:
        if not isinstance(task, dict):
            continue
        yield task
        for key in ("block", "rescue", "always"):
            yield from _walk(task.get(key))
