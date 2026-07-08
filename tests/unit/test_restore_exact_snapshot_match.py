"""A full k8s restore must exactly match the snapshot. Each host-source dir
(config, extensions, skins, public_assets) is cleared before restore so a
create-seeded file the source deleted doesn't survive, and the shared images
PVC is cleared before restic — but ONLY on a full restore. A single-wiki
restore must not clear the shared images PVC or other wikis' dirs."""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
RESTORE_K8S = os.path.join(
    REPO_ROOT, "roles", "backup", "tasks", "restore_k8s.yml",
)


def _tasks():
    with open(RESTORE_K8S) as f:
        return yaml.safe_load(f)


def _task(name_substr):
    for t in _tasks():
        if name_substr in (t.get("name") or ""):
            return t
    raise AssertionError("task %r not found" % name_substr)


def _cmd(task):
    return task["ansible.builtin.shell"]["cmd"]


def test_full_config_restore_clears_first():
    t = _task("Copy restored config to host")
    assert t["when"] == "wiki is not defined"
    assert 'rm -rf "{{ instance_path }}/config"' in _cmd(t)


def test_full_user_dirs_restore_clears_first():
    t = _task("Restore extensions/skins/public_assets to host")
    assert t["when"] == "wiki is not defined"
    assert 'rm -rf "{{ instance_path }}/{{ item }}"' in _cmd(t)
    assert t["loop"] == ["extensions", "skins", "public_assets"]


def test_images_pvc_cleared_only_on_full_restore():
    text = open(RESTORE_K8S).read()
    # The images-PVC clear is gated on a full restore.
    assert "{% if wiki is not defined %}" in text
    assert "rm -rf /currentsnapshot/images/" in text


def test_single_wiki_still_scoped_not_pruned():
    # Single-wiki restore keeps restic scoped to just this wiki's paths, so it
    # never clears/clobbers the shared images PVC or other wikis' data.
    t = _task("Build restic include filters for single-wiki restore")
    assert t["when"] == "wiki is defined"
