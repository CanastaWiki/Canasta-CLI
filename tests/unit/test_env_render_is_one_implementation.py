""".env must be rendered by one implementation, not a copy per call site.

render_compose.yml omitted a placeholder with no value; pull_compose.yml had
its own copy that did not, and wrote the `KEY=` the first exists to prevent.
Pull is the path every host that is not the source takes, so the copy without
the guard was the one that ran most.

A guard copied a second time can come apart again the same way, so the test is
that there is no second copy — not that both copies agree.
"""
import os
import re

import yaml


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GITOPS_TASKS = os.path.join(REPO_ROOT, "roles", "gitops", "tasks")
RENDER_ENV = os.path.join(GITOPS_TASKS, "_render_env.yml")


def _walk(tasks):
    for task in tasks or []:
        yield task
        for key in ("block", "rescue", "always"):
            for nested in _walk(task.get(key)):
                yield nested


def _tasks(path):
    with open(path) as fh:
        return list(_walk(yaml.safe_load(fh)))


def _render_task(path):
    for task in _tasks(path):
        if (task.get("name") or "") == "Render .env from template":
            return task
    return None


def test_only_the_shared_file_writes_env():
    writers = []
    for name in sorted(os.listdir(GITOPS_TASKS)):
        if not name.endswith(".yml"):
            continue
        path = os.path.join(GITOPS_TASKS, name)
        for task in _tasks(path):
            copy = task.get("ansible.builtin.copy") or {}
            if str(copy.get("dest", "")).endswith("/.env"):
                writers.append(name)
    assert writers == ["_render_env.yml"], (
        "a second place rendering .env is how the two came apart: %s" % writers
    )


def test_both_call_sites_include_it():
    for caller, fact in (("render_compose.yml", "_render_vars"),
                         ("pull_compose.yml", "_pull_vars")):
        task = _render_task(os.path.join(GITOPS_TASKS, caller))
        assert task, "%s no longer renders .env" % caller
        assert task["ansible.builtin.include_tasks"] == "_render_env.yml"
        assert task["vars"]["env_render_vars"] == "{{ %s }}" % fact


def test_the_shared_render_reads_its_vars_from_the_caller():
    body = str(_render_task(RENDER_ENV)["ansible.builtin.copy"]["content"])
    assert "env_render_vars[placeholder]" in body
    # Neither caller's own fact name may leak into the shared file.
    # Word-bounded: env_render_vars contains _render_vars as a substring.
    for leaked in (r"\b_render_vars\[", r"\b_pull_vars\["):
        assert not re.search(leaked, body), leaked


def test_the_rendered_file_stays_owner_only():
    assert _render_task(RENDER_ENV)["ansible.builtin.copy"]["mode"] == "0600"
