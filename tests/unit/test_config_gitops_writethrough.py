"""On a gitops instance, `config set` must persist EVERY key to the durable
source: placeholder/secret keys to vars.yaml, everything else to the
env.template literal. A non-placeholder key left as a .env-only write is
reverted the next time a pull / config regenerate re-renders .env."""

import os

import yaml


REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
UPDATE_VAR = os.path.join(
    REPO_ROOT, "roles", "config", "tasks", "_update_gitops_var.yml"
)


def _tasks():
    with open(UPDATE_VAR) as f:
        return yaml.safe_load(f)


def _find(name_substring):
    return next(
        (t for t in _tasks() if name_substring in t.get("name", "")), None)


def _lineinfile(task):
    return (task.get("ansible.builtin.lineinfile")
            or task.get("lineinfile")) if isinstance(task, dict) else None


def test_placeholder_key_still_writes_vars_yaml():
    block = _find("vars.yaml if key has a gitops placeholder")
    assert block is not None
    assert "_ugv_placeholder != ''" in str(block.get("when", ""))


def test_non_placeholder_key_updates_env_template_literal():
    block = _find("env.template literal when key has no placeholder")
    assert block is not None, (
        "config set must persist a non-placeholder key to env.template, or it "
        "is a .env-only write that the next render reverts"
    )
    assert "_ugv_placeholder == ''" in str(block.get("when", ""))
    li = next(
        (t for t in block.get("block", [])
         if (_lineinfile(t) or {}).get("path", "").endswith("env.template")),
        None,
    )
    assert li is not None, "must lineinfile the key's literal in env.template"
    assert "_ugv_key" in str(_lineinfile(li).get("regexp", ""))
    assert "_ugv_value" in str(_lineinfile(li).get("line", ""))
