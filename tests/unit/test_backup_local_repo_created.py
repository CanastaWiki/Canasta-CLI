"""A local-path restic repository must be created, not assumed.

Docker creates a missing bind-mount source directory; podman refuses:

    $ podman run --rm -v /tmp/nodir:/x docker.io/library/alpine true
    Error: statfs /tmp/nodir: no such file or directory

    $ docker run --rm -v /tmp/nodir2:/x alpine true
    (succeeds, directory created)

run_backup.yml mounted RESTIC_REPOSITORY straight into the restic
container and never created it, so on Docker the directory appeared as a
side effect of the mount and on podman `canasta backup init` failed
before restic ran at all.

It only stat'd the repo's *parent*, and that is for reclaiming ownership
afterwards — not for creating anything.
"""

import os

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
RUN_BACKUP = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "run_backup.yml")


def _tasks():
    out = []

    def walk(node):
        if isinstance(node, dict):
            out.append(node)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for i in node:
                walk(i)

    with open(RUN_BACKUP) as f:
        walk(yaml.safe_load(f))
    return out


def _names():
    return [str(t.get("name", "")) for t in _tasks() if isinstance(t, dict)]


def _named(needle):
    return next(
        (t for t in _tasks() if needle.lower() in str(t.get("name", "")).lower()),
        None)


class TestTheRepoDirectoryIsCreated:
    def test_a_creating_task_exists(self):
        assert _named("Ensure the local repo directory exists"), (
            "nothing creates RESTIC_REPOSITORY, so podman fails the mount "
            "with statfs before restic runs"
        )

    def test_it_creates_the_repo_not_the_parent(self):
        body = _named("Ensure the local repo directory exists")[
            "ansible.builtin.file"]
        assert body["state"] == "directory"
        path = str(body["path"])
        assert "_backup_local_repo" in path
        assert "dirname" not in path, (
            "the parent is stat'd for ownership reclaim; it is the repo "
            "itself that has to exist for the bind mount"
        )

    def test_it_only_applies_to_a_local_repo(self):
        # S3 and other remote backends involve no bind mount.
        assert "_backup_local_repo | length > 0" in str(
            _named("Ensure the local repo directory exists").get("when", ""))


class TestOrdering:
    def test_it_runs_before_restic(self):
        names = _names()
        create_at = names.index("Ensure the local repo directory exists")
        restic_at = names.index("Run restic container")
        assert create_at < restic_at

    def test_it_runs_after_the_repo_path_is_known(self):
        names = _names()
        detect_at = names.index("Detect local repo (from .env or -r flag)")
        create_at = names.index("Ensure the local repo directory exists")
        assert detect_at < create_at


class TestItDoesNotEscalate:
    def test_no_become(self):
        # Created as the connecting user so the post-run chown is a no-op
        # rather than a correction; root here would defeat that.
        assert "become" not in _named("Ensure the local repo directory exists")
