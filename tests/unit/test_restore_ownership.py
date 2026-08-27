"""Guard for restore file-ownership normalization (Compose).

The Compose restore copies files to the host with `cp -a`, which preserves the
snapshot's numeric uids. On a cross-host restore those ids need not map to a
real user, so `.env` (mode 0600) came back unreadable by the CLI user and
aborted the restore before the DB import.

Normalizing only the top-level files left config/, extensions/, skins/,
public_assets/ and images/ carrying the source host's uid, which git's
safe.directory check refuses and chmod will not touch. The whole restored
tree must be chowned to the instance dir's owner, and not by way of the
post-restore restart: the entrypoint's make_dir_writable only chgrps and
chmods, so a restart never repairs ownership at all.
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
COMPOSE = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "restore_instance.yml")


def _flatten(tasks):
    for t in tasks or []:
        if not isinstance(t, dict):
            continue
        yield t
        for key in ("block", "rescue", "always"):
            if key in t:
                yield from _flatten(t[key])


def _task(name):
    with open(COMPOSE) as f:
        tasks = list(_flatten(yaml.safe_load(f)))
    return next(t for t in tasks if t.get("name") == name)


def _copy_task():
    return _task("Copy files from volume to host")


def test_copy_step_normalizes_ownership_to_instance_owner():
    cmd = _copy_task()["ansible.builtin.shell"]["cmd"]
    # Derives the target from the instance dir owner (not a hard-coded uid)...
    assert 'stat -c "%u:%g" /install' in cmd, (
        "restore must derive ownership from the instance dir, not assume a uid")
    # ...and chowns restored files to it.
    assert "chown" in cmd, (
        "restore must chown restored files or a cross-host restore leaves .env "
        "owned by an unmapped uid and unreadable by the CLI user")


def test_copy_step_normalizes_the_whole_restored_tree():
    cmd = _copy_task()["ansible.builtin.shell"]["cmd"]
    assert "chown -R" in cmd, (
        "a non-recursive chown leaves the restored directories owned by the "
        "source host's uid, which git refuses and chmod cannot fix"
    )
    assert '[ -f "$f" ] && chown' not in cmd, (
        "the top-level-files-only normalization is what left config/, "
        "extensions/, skins/ and public_assets/ stranded"
    )


def test_single_wiki_restore_normalizes_what_it_restored():
    cmd = _task(
        "Copy single wiki's files from volume to host"
    )["ansible.builtin.shell"]["cmd"]
    assert 'stat -c "%u:%g" /install' in cmd
    assert "chown -R" in cmd, (
        "a single-wiki restore copies with cp -a too, so it strands the same "
        "uids across the wiki's settings, images and public assets"
    )
