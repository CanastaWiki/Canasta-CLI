"""A Compose backup should not copy the instance before snapshotting it.

Every `backup create` used to `rm -rf` a staging volume and `cp -a` the
whole instance into it — config, extensions, images, skins,
public_assets — before restic started. On an instance with a 46 GB
images/ tree that is a 46 GB local copy on every run, scheduled ones
included, and a second copy of the instance sitting on disk between runs.
The snapshot itself is incremental and cheap after the first, so the
staging copy dominated the runtime and got no cheaper over time.

The sources now reach restic as read-only mounts at the paths they would
have been copied to, which is what the Kubernetes Job already did.

Docker Desktop is the exception: restic cannot read a host bind mount
there at all — every read returns EIO and the snapshot comes back empty —
so the staging copy has to stay for it. These tests hold both halves of
that: the mounts are the default, the copy survives for the runtime that
needs it, and neither one can quietly become the other.
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
RUN_BACKUP = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "run_backup.yml")
RESTORE = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "restore_instance.yml")


def _walk(tasks):
    for t in tasks or []:
        if not isinstance(t, dict):
            continue
        yield t
        for nested in ("block", "rescue", "always"):
            if nested in t:
                yield from _walk(t[nested])


def _tasks(path):
    with open(path) as f:
        return list(_walk(yaml.safe_load(f)))


def _shell_cmds(path):
    out = []
    for t in _tasks(path):
        sh = t.get("ansible.builtin.shell") or t.get("shell")
        if isinstance(sh, dict) and sh.get("cmd"):
            out.append(str(sh["cmd"]))
        elif isinstance(sh, str):
            out.append(sh)
    return out


def _restic_cmd():
    for t in _tasks(RUN_BACKUP):
        if t.get("name") == "Run restic container":
            return t["ansible.builtin.shell"]["cmd"]
    raise AssertionError("run_backup.yml has no 'Run restic container' task")


class TestNoStagingCopy:
    def test_the_copy_runs_only_for_a_runtime_restic_cannot_read(self):
        staging = [
            t for t in _tasks(RUN_BACKUP)
            if t.get("name") == "Stage files into backup volume"
        ]
        assert len(staging) == 1
        assert "_backup_staged | length > 0" in str(staging[0].get("when")), (
            "a staging copy is a full second copy of the instance on every "
            "backup, scheduled ones included, so it must not run wherever "
            "restic can read the sources directly"
        )

    def test_docker_desktop_is_the_only_runtime_that_stages(self):
        """restic reads nothing through Docker Desktop's file sharing: every
        read of a host bind mount returns EIO, the snapshot comes back empty
        and the backup fails. Verified on restic 0.16.4, 0.17.3 and 0.19.0,
        read-write as well as :ro."""
        for t in _tasks(RUN_BACKUP):
            if t.get("name") == "Decide how the sources reach restic":
                decide = t["ansible.builtin.set_fact"]["_backup_mount_sources"]
                assert "Docker Desktop" in decide
                assert "not in" in decide, (
                    "mounting has to be the default; staging is the exception"
                )
                return
        raise AssertionError("no runtime decision task")

    def test_sources_are_mounted_read_only_into_restic(self):
        cmd = _restic_cmd()
        assert "-v {{ src }}:{{ dst }}:ro" in cmd, (
            "each source must reach restic as a read-only mount at the path "
            "it would have been copied to"
        )

    def test_snapshot_paths_are_unchanged(self):
        """The mount target is the source's own dst from backup_volumes —
        the same /currentsnapshot/<name> the copy used — so existing
        snapshots, parent detection and retention grouping still line up.
        Confirmed against a Linux daemon: the two shapes produce identical
        listings, and the mounted snapshot takes the staged one as its
        parent."""
        cmd = _restic_cmd()
        assert "_backup_sources.items()" in cmd
        for t in _tasks(RUN_BACKUP):
            if t.get("name") == "Split the sources between mounting and staging":
                sf = t["ansible.builtin.set_fact"]
                assert "backup_volumes" in sf["_backup_sources"]
                assert "backup_volumes" in sf["_backup_staged"]
                return
        raise AssertionError("no source-splitting task")

    def test_a_source_is_never_both_mounted_and_staged(self):
        for t in _tasks(RUN_BACKUP):
            if t.get("name") == "Split the sources between mounting and staging":
                sf = t["ansible.builtin.set_fact"]
                assert "_backup_mount_sources" in sf["_backup_sources"]
                assert "_backup_mount_sources" in sf["_backup_staged"]
                return
        raise AssertionError("no source-splitting task")

    def test_the_staging_volume_is_created_before_anything_stages_into_it(self):
        names = [t.get("name") for t in _tasks(RUN_BACKUP)]
        assert names.index("Ensure backup volume exists") < names.index(
            "Stage files into backup volume")

    def test_the_staging_volume_is_not_created_when_nothing_stages(self):
        for t in _tasks(RUN_BACKUP):
            if t.get("name") == "Ensure backup volume exists":
                assert "not _backup_sources" in str(t.get("when")), (
                    "a backup that mounts its sources has no use for the "
                    "staging volume"
                )
                return
        raise AssertionError("no volume-create task")


class TestRestoreStillStages:
    """restic restores into the volume and a second step copies onto the
    host, so a half-finished restore never touches the live instance. That
    direction keeps its copy."""

    def test_restic_falls_back_to_the_volume_with_no_sources(self):
        cmd = _restic_cmd()
        assert "-v {{ _bvol }}:/currentsnapshot" in cmd

    def test_restore_reads_the_volume(self):
        mounts = " ".join(_shell_cmds(RESTORE))
        assert "canasta-backup-{{ instance_path | basename }}:/currentsnapshot" \
            in mounts
