"""A failed create must not leave an undeletable instance directory.

Under rootless Podman the containers write through a user-namespace id
map, so files they create in the instance directory are owned by a
subuid. `ansible.builtin.file: state: absent` removes from this
process's namespace and gets EACCES, so the rescue that exists to leave
no trace instead leaves a directory the operator cannot delete either --
and a non-empty directory blocks the next create.

`podman unshare` enters the namespace where those ids map back. delete
already does this (#1267); create's rescue is the counterpart.
"""

import os

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
CREATE = os.path.join(REPO_ROOT, "playbooks", "create.yml")


def _rescue_tasks():
    with open(CREATE) as f:
        doc = yaml.safe_load(f)
    for play in doc:
        if isinstance(play, dict) and "rescue" in play:
            return play["rescue"]
    raise AssertionError("create.yml has no rescue block")


def _task(substring):
    for t in _rescue_tasks():
        if substring in str(t.get("name", "")):
            return t
    return None


class TestRescueClearsIdMappedFiles:
    def test_it_unshares_before_removing(self):
        purge = _task("remove id-mapped files")
        assert purge is not None, (
            "create's rescue must clear id-mapped files, or a failed "
            "create on rootless Podman strands an undeletable directory"
        )
        assert "podman unshare" in str(purge["ansible.builtin.command"]["cmd"]), (
            "removal has to happen inside the user namespace"
        )

    def test_it_runs_before_the_directory_removal(self):
        names = [str(t.get("name", "")) for t in _rescue_tasks()]
        purge = next(i for i, n in enumerate(names) if "id-mapped files" in n)
        remove = next(i for i, n in enumerate(names) if "remove directory" in n)
        assert purge < remove, (
            "the id-mapped files must go first; otherwise `file: state: "
            "absent` still hits EACCES and the directory survives"
        )

    def test_it_only_fires_for_rootless_podman(self):
        conditions = [str(c) for c in _task("remove id-mapped files")["when"]]
        assert any("_create_podman_rootless" in c for c in conditions), (
            "gated on the rootless probe, so Docker is untouched"
        )

    def test_the_probe_is_gated_on_a_podman_runtime(self):
        conditions = [str(c) for c in _task("Detect rootless Podman")["when"]]
        assert any("inspect_command" in c and "podman" in c
                   for c in conditions), (
            "do not shell out to `podman info` on a Docker host"
        )


class TestRescuePurgeIsGuardedLikeDelete:
    """`find -delete` on a bad path is unrecoverable, so guard it."""

    def test_it_requires_a_nontrivial_absolute_path(self):
        conditions = [str(c) for c in _task("remove id-mapped files")["when"]]
        assert any("length > 5" in c for c in conditions), (
            "a short path could be a corrupted registry or unset variable"
        )
        assert any("match('^/')" in c for c in conditions), (
            "relative paths would resolve against an unexpected cwd"
        )

    def test_it_still_respects_the_operators_directory(self):
        conditions = [str(c) for c in _task("remove id-mapped files")["when"]]
        assert any("instance_dir_was_created" in c for c in conditions), (
            "a pre-existing directory belongs to the operator; the rescue "
            "must not empty one this run did not create"
        )
        assert any("keep_config" in c for c in conditions), (
            "--keep-config must suppress the purge as it does the removal"
        )
