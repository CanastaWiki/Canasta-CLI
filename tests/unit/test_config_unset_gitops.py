"""On a gitops instance, `config unset` must remove the key from the durable
source, not just .env: a placeholder key from vars.yaml and its env.template
placeholder line, a literal key from env.template. Otherwise the next pull /
config regenerate re-renders .env and resurrects the key. Mirrors the set-side
gitops tests (test_config_gitops_writethrough).
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
UNSET = os.path.join(REPO_ROOT, "roles", "config", "tasks", "unset.yml")
UNSET_CONFIG = os.path.join(
    REPO_ROOT, "roles", "config", "tasks", "_unset_config.yml"
)
REMOVE_VAR = os.path.join(
    REPO_ROOT, "roles", "config", "tasks", "_remove_gitops_var.yml"
)
DISPATCH = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "config_remove_gitops_vars.yml"
)


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _find(tasks, name_substring):
    return next(
        (t for t in tasks if name_substring in t.get("name", "")), None)


def _when(task):
    w = task.get("when", [])
    return " ".join(w if isinstance(w, list) else [str(w)])


def _lineinfile(task):
    return (task.get("ansible.builtin.lineinfile")
            or task.get("lineinfile")) if isinstance(task, dict) else None


# --- the .env path wires the gitops check + dispatch (mirrors _set_config.yml) ---

def test_unset_dispatches_secret_and_env_paths():
    """--secret keys skip .env validation and side effects; the two paths
    must stay separate."""
    tasks = _load(UNSET)
    secret = _find(tasks, "Remove opaque secret values")
    env = _find(tasks, "Remove .env config values")
    assert secret is not None and env is not None
    assert str(secret["ansible.builtin.include_tasks"]).endswith(
        "_unset_secret.yml")
    assert str(env["ansible.builtin.include_tasks"]).endswith(
        "_unset_config.yml")
    assert "secret" in _when(secret) and "not (secret" in _when(env)


def test_unset_checks_for_gitops_instance():
    tasks = _load(UNSET_CONFIG)
    stat = _find(tasks, "Check for gitops instance")
    assert stat is not None, "unset must stat .gitops-host like set does"
    st = stat.get("ansible.builtin.stat") or stat.get("stat") or {}
    assert st.get("path", "").endswith(".gitops-host")


def test_unset_dispatches_to_orchestrator_role():
    tasks = _load(UNSET_CONFIG)
    dispatch = _find(tasks, "Remove gitops vars for unset keys")
    assert dispatch is not None, "unset must dispatch gitops removal"
    inc = str(dispatch.get("ansible.builtin.include_tasks", ""))
    assert inc.endswith("config_remove_gitops_vars.yml"), (
        "must remove the vars through the orchestrator role")
    assert "_config_gitops_stat.stat.exists" in _when(dispatch)


def test_compose_branch_removes_vars():
    tasks = _load(DISPATCH)
    compose = [t for t in tasks
               if str(t.get("ansible.builtin.include_tasks", ""))
               .endswith("_remove_gitops_vars.yml")]
    assert compose, "Compose must remove vars from the gitops source"


# --- Compose removal: placeholder -> vars.yaml + env.template line ----------

def test_placeholder_key_dropped_from_vars_yaml():
    tasks = _load(REMOVE_VAR)
    block = _find(tasks, "vars.yaml and env.template when key has a placeholder")
    assert block is not None
    assert "_rgv_placeholder != ''" in _when(block)
    # It must actually drop the placeholder from the vars dict.
    drop = next(
        (t for t in block.get("block", [])
         if "Drop placeholder from vars" in t.get("name", "")), None)
    assert drop is not None, "must remove the placeholder from vars.yaml"


def test_placeholder_key_line_removed_from_env_template():
    tasks = _load(REMOVE_VAR)
    block = _find(tasks, "vars.yaml and env.template when key has a placeholder")
    li = next(
        (t for t in block.get("block", [])
         if (_lineinfile(t) or {}).get("path", "").endswith("env.template")),
        None,
    )
    assert li is not None, "must remove the placeholder line from env.template"
    assert _lineinfile(li).get("state") == "absent"


# --- Compose removal: literal key -> env.template ---------------------------

def test_non_placeholder_key_removed_from_env_template():
    tasks = _load(REMOVE_VAR)
    block = _find(tasks, "env.template literal when key has no placeholder")
    assert block is not None, (
        "config unset must remove a non-placeholder key's literal from "
        "env.template, or the next render resurrects it")
    assert "_rgv_placeholder == ''" in _when(block)
    li = next(
        (t for t in block.get("block", [])
         if (_lineinfile(t) or {}).get("path", "").endswith("env.template")),
        None,
    )
    assert li is not None, "must lineinfile the literal absent in env.template"
    assert _lineinfile(li).get("state") == "absent"
    assert "_config_key" in str(_lineinfile(li).get("regexp", ""))
