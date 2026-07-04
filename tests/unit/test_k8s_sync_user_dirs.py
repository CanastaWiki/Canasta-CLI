"""Structural guards for the K8s user-extensions/user-skins/public-assets
PVC mirror.

On K8s, custom extensions/skins and public_assets live in PVCs that only
k8s_sync_user_dirs.yml populates. The sync must MIRROR the instance dirs —
copy present entries AND prune entries removed from the instance dir — so that
removing a custom extension/skin/asset actually takes effect on the next
start, matching the Compose inotify monitor. Earlier the sync was additive
only (and gated on the dir having content), so a removal lingered in the PVC
and got re-linked. These tests lock the mirror wiring in place; the runtime
behavior is covered by the live K8s e2e.
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


def test_public_assets_is_mirrored():
    # public_assets is a PVC on k8s like extensions/skins, and nothing else
    # populates it, so the mirror must cover it too — otherwise logos/favicon
    # referenced from settings ($wgLogos, $wgFavicon) 404 in the pod.
    body = _text()
    tasks = _tasks()
    # Helper pod mounts the public-assets PVC at /sync/public_assets.
    assert "public-assets" in body
    assert "/sync/public_assets" in body
    # The find + list loops include public_assets alongside extensions/skins.
    find = next(t for t in tasks
                if t.get("name") == "Find instance top-level entries per dir")
    assert "public_assets" in find["loop"]
    # Prune covers the public_assets PVC (results[2], loop order ext/skins/pa).
    prune_fact = next(t for t in tasks
                      if t.get("name") == "Compute stale PVC entries to prune")
    expr = prune_fact["ansible.builtin.set_fact"]["_ud_prune"]
    assert "public_assets/" in expr
    assert "results[2]" in expr


def test_managed_entry_cleared_before_copy():
    # kubectl cp of a dir onto an existing dir NESTS it (/sync/<dir>/<name>/<name>)
    # instead of replacing it, so an updated entry must be removed before the
    # copy or its new code is orphaned one level deep. Guard the clear step and
    # its order (clear must precede the cp).
    tasks = _tasks()
    names = _names(tasks)
    assert "Clear each managed entry before copy" in names, (
        "re-synced (updated) entries must be removed before kubectl cp, else "
        "the copy nests under the existing dir and the old code keeps loading"
    )
    clear = next(t for t in tasks if t.get("name") == "Clear each managed entry before copy")
    cmd = clear["ansible.builtin.command"]["cmd"]
    assert "rm -rf" in cmd
    # Clears exactly the entries about to be copied (same loop as the cp step).
    assert clear["loop"] == "{{ _ud_entries.results | subelements('files', skip_missing=True) }}"
    assert names.index("Clear each managed entry before copy") < \
        names.index("kubectl cp each entry into its PVC")
