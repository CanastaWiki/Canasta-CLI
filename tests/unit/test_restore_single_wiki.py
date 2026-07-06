"""Structural guards for single-wiki restore (`canasta backup restore -w`).

`-w`/`--wiki` used to be accepted by the CLI but ignored by every restore
task, so a "single wiki" restore silently restored the whole instance —
overwriting all wikis' databases plus shared state (.env, global config,
extensions, skins). These tests lock the scoping wiring on both orchestrators:
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
K8S = os.path.join(REPO_ROOT, "roles", "backup", "tasks", "restore_k8s.yml")


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
    return next((t for t in _tasks(path) if t.get("name") == name), None)


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
    full = _by_name(COMPOSE, "Import each wiki database dump")
    assert full is not None and "wiki is not defined" in _when(full)
    one = _by_name(COMPOSE, "Import the single restored wiki's database dump")
    assert one is not None and "wiki is defined" in _when(one)


def test_compose_env_password_preserve_gated_off_for_single_wiki():
    # There are two "Preserve DB password in restored .env" tasks (Compose +
    # K8s block); both must be gated off for a single-wiki restore.
    preserves = [t for t in _tasks(COMPOSE)
                 if t.get("name") == "Preserve DB password in restored .env"]
    assert len(preserves) == 2
    assert all("wiki is not defined" in _when(t) for t in preserves), (
        "single-wiki restore must not rewrite the shared .env")


# --- Kubernetes (restore_k8s.yml) ---

def test_k8s_builds_include_filters_for_single_wiki():
    inc = _by_name(K8S, "Build restic include filters for single-wiki restore")
    assert inc is not None and "wiki is defined" in _when(inc)
    val = inc["ansible.builtin.set_fact"]["_restore_include"]
    for path in ("config/settings/wikis/", "config/backup/db_",
                 "images/", "public_assets/"):
        assert path in val, "include filter must cover %s" % path


def test_k8s_restic_restore_uses_the_include_filter():
    with open(K8S) as f:
        text = f.read()
    assert "restore {{ _resolved_snapshot }}{{ _restore_include }}" in text, (
        "restic restore must apply the single-wiki include filter")


def test_k8s_full_host_copies_are_gated_off_for_single_wiki():
    for name in ("Copy restored config to host",
                 "Copy restored .env to host",
                 "Restore extensions/skins/public_assets to host"):
        t = _by_name(K8S, name)
        assert t is not None, "missing task: %s" % name
        assert "wiki is not defined" in _when(t), (
            "%s must be gated off for single-wiki restore" % name)


def test_k8s_has_scoped_single_wiki_restores():
    settings = _by_name(K8S, "Copy restored single wiki settings to host")
    assert settings is not None and "wiki is defined" in _when(settings)
    assets = _by_name(K8S, "Restore single wiki public assets to host")
    assert assets is not None and "wiki is defined" in _when(assets)


def test_k8s_fails_when_wiki_absent_from_snapshot():
    fail = _by_name(K8S, "Fail when the target wiki is absent from the snapshot")
    assert fail is not None, (
        "a wiki missing from the snapshot must fail, not silently no-op")
