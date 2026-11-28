"""Structural guards for single-wiki restore (`canasta backup restore -w`).

`-w`/`--wiki` used to be accepted by the CLI but ignored by every restore
task, so a "single wiki" restore silently restored the whole instance —
overwriting all wikis' databases plus shared state (.env, global config,
extensions, skins). These tests lock the scoping wiring on Compose:
a scoped restore touches only the target wiki's DB and per-wiki files, and the
shared-state steps are gated off when `wiki` is defined. Runtime behavior is
covered by the live e2e.
"""

import os

import yaml


REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
RESTORE = os.path.join(REPO_ROOT, "roles", "backup", "tasks", "restore.yml")
COMPOSE = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "restore_instance.yml")


def _flatten(tasks):
    """Yield every task, descending into block/rescue/always."""
    for t in tasks or []:
        yield t
        for key in ("block", "rescue", "always"):
            if key in t:
                yield from _flatten(t[key])


def _tasks(path):
    with open(path) as f:
        return list(_flatten(yaml.safe_load(f)))


def _by_name(path, name):
    task = next((t for t in _tasks(path) if t.get("name") == name), None)
    return task


def _when(task):
    """Normalize a task's `when` (str or list) to one joined string."""
    w = task.get("when", [])
    if isinstance(w, list):
        return " and ".join(str(x) for x in w)
    return str(w)


# --- shared validation (restore.yml) ---

def test_restore_validates_target_wiki_exists():
    fail = _by_name(RESTORE, "Fail when the target wiki is not in this instance")
    assert fail is not None, (
        "single-wiki restore must validate the wiki exists in the instance")
    assert "wiki not in" in _when(fail)


# --- Compose (restore_instance.yml) ---

def test_compose_wholesale_copy_is_gated_off_for_single_wiki():
    full = _by_name(COMPOSE, "Copy files from volume to host")
    assert full is not None
    assert "wiki is not defined" in _when(full), (
        "the wholesale copy must not run for a single-wiki restore")


def test_compose_has_scoped_single_wiki_copy():
    scoped = _by_name(COMPOSE, "Copy single wiki's files from volume to host")
    assert scoped is not None and "wiki is defined" in _when(scoped)
    cmd = scoped["ansible.builtin.shell"]["cmd"]
    # Only per-wiki paths, addressed via the $W env var (never the shared dirs).
    for rel in ("config/settings/wikis/$W", "images/$W", "public_assets/$W"):
        assert rel in cmd, "scoped copy must restore %s" % rel


def test_compose_db_import_is_scoped():
    task = _by_name(COMPOSE, "Import each wiki database dump")
    assert task is not None
    assert "[wiki] if wiki is defined else _restore_import_wikis" in task["loop"]


def _env_password_preserves():
    """The .env password-preserve tasks in restore_instance.yml."""
    with open(COMPOSE) as f:
        tasks = yaml.safe_load(f)
    return [t for t in _flatten(tasks)
            if t.get("name") == "Preserve DB password in restored .env"]


def test_compose_env_password_preserve_gated_off_for_single_wiki():
    preserves = _env_password_preserves()
    assert len(preserves) == 1
    assert all("wiki is not defined" in _when(t) for t in preserves), (
        "single-wiki restore must not rewrite the shared .env")
