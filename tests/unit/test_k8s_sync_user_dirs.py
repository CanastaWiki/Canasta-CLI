"""Structural guards for the K8s user-extensions/user-skins PVC mirror.

On K8s, custom extensions/skins live in PVCs that only k8s_sync_user_dirs.yml
populates. The sync must MIRROR the instance dirs — copy present entries AND
prune entries removed from the instance dir — so that removing a custom
extension/skin actually takes effect on the next start, matching the Compose
inotify monitor. Earlier the sync was additive only (and gated on the dir
having content), so a removal lingered in the PVC and got re-linked. These
tests lock the mirror wiring in place; the runtime behavior is covered by the
live K8s e2e.
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
SYNC = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "k8s_sync_user_dirs.yml")


def _tasks():
    with open(SYNC) as f:
        return yaml.safe_load(f)


def _text():
    with open(SYNC) as f:
        return f.read()


def _names(tasks):
    return [t.get("name", "") for t in tasks]


def test_sync_is_not_gated_on_dir_having_content():
    # The mirror must run unconditionally so a removal is pruned even when the
    # instance dir is now empty (the last custom entry was deleted). Guard
    # against the old `when: _ud_dirs | length > 0` content gate creeping back.
    body = _text()
    assert "_ud_dirs" not in body, (
        "sync must not gate on a 'dirs with content' fact — removals would "
        "not be pruned once the dir is empty"
    )
    tasks = _tasks()
    # The helper-pod creation must not sit behind a content/when guard.
    create = next(t for t in tasks if t.get("name") == "Create the helper pod")
    assert "when" not in create


def test_sync_computes_and_prunes_stale_entries():
    body = _text()
    tasks = _tasks()
    names = _names(tasks)
    assert "Compute stale PVC entries to prune" in names
    assert "Prune removed entries from the PVCs" in names
    # Stale set is "in the PVC, not in the instance dir": a difference() of the
    # PVC listing against the instance find results.
    assert "difference" in body
    prune = next(t for t in tasks if t.get("name") == "Prune removed entries from the PVCs")
    cmd = prune["ansible.builtin.command"]["cmd"]
    assert "rm -rf" in cmd
    assert prune["loop"] == "{{ _ud_prune }}"


def test_prune_never_touches_lost_found():
    # ext-family RWO volumes carry a lost+found dir that is not a managed
    # entry; pruning it would be wrong (and rm could fail on it).
    assert "lost+found" in _text()


def test_sync_still_copies_present_entries():
    tasks = _tasks()
    cp = next(t for t in tasks if t.get("name") == "kubectl cp each entry into its PVC")
    cmd = cp["ansible.builtin.command"]["cmd"]
    assert "kubectl cp" in cmd
    assert cp["loop"] == "{{ _ud_entries.results | subelements('files', skip_missing=True) }}"
