"""A local-path restic repo must end up owned by the operator.

restic runs as root inside its container, so a repo on a bind-mounted
host path is created root-owned — with `config` at mode 0400, so even
reading the metadata needs sudo. The operator then cannot prune or
remove their own backups, and a torn-down instance leaves an
undeletable directory behind.

K8s already reclaims ownership (#1205). This holds the Compose path to
the same contract, and checks it happens in `always` — a repo left
root-owned after a *failed* run is precisely when someone needs to get
into it.
"""

import os

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
COMPOSE_BACKUP = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "run_backup.yml")
K8S_BACKUP = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "k8s_run_backup.yml")


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f) or []


def _walk(node, out):
    if isinstance(node, dict):
        out.append(node)
        for v in node.values():
            _walk(v, out)
    elif isinstance(node, list):
        for i in node:
            _walk(i, out)


def _tasks(path):
    out = []
    _walk(_load(path), out)
    return out


def _reclaim_task():
    return next(
        (t for t in _tasks(COMPOSE_BACKUP)
         if "reclaim" in str(t.get("name", "")).lower()),
        None,
    )


class TestComposeReclaimsOwnership:
    def test_a_reclaim_task_exists(self):
        assert _reclaim_task(), (
            "the Compose backup path never chowns a local-path repo, so it "
            "stays root-owned and the operator cannot manage their backups "
            "(K8s fixed this in #1205)"
        )

    def test_it_chowns_to_the_parent_directory_owner(self):
        argv = [str(a) for a in _reclaim_task().get(
            "ansible.builtin.command", {}).get("argv", [])]
        assert "chown" in argv, "the reclaim task does not chown"
        joined = " ".join(argv)
        # The target is computed per runtime rather than interpolated here.
        # The chown runs inside a container, and rootless Podman maps
        # container uid N to subuid_base + N - 1, so passing the host uid
        # through hands the repo to a subuid nobody can use. The Docker
        # branch still derives from the parent directory, asserted below;
        # both branches are covered in
        # test_backup_ownership_reclaim_namespace.py.
        assert "_backup_chown_target" in joined, (
            "ownership must come from the repo's parent directory, not a "
            "guess at who ran the command"
        )
        chooser = next(
            t for t in _tasks(COMPOSE_BACKUP)
            if "Choose the in-container ownership target"
            in str(t.get("name", ""))
        )
        assert "_backup_repo_parent.stat.uid" in str(
            chooser["ansible.builtin.set_fact"]["_backup_chown_target"])
        assert "-R" in argv, "the whole repo tree needs chowning, not just its root"

    def test_it_runs_even_when_restic_fails(self):
        # The restic task has no failed_when, so a non-zero rc aborts the
        # block. Only `always` still reclaims ownership in that case.
        wrapper = next(
            t for t in _tasks(COMPOSE_BACKUP)
            if isinstance(t.get("always"), list)
            and any("reclaim" in str(a.get("name", "")).lower()
                    for a in t["always"])
        )
        assert wrapper, "the reclaim must sit in `always`, not follow the run"
        block_names = [str(b.get("name", "")) for b in wrapper.get("block", [])]
        assert any("restic container" in n for n in block_names), (
            "the guarded block should contain the restic run itself"
        )

    def test_a_failed_chown_does_not_mask_the_backup_result(self):
        task = _reclaim_task()
        assert task.get("failed_when") is False, (
            "a chown failure must not turn a successful backup into a "
            "failed one"
        )


class TestBothOrchestratorsAgree:
    def test_k8s_still_reclaims_too(self):
        # If the K8s side ever loses this, the asymmetry returns the other
        # way round and this test says so.
        with open(K8S_BACKUP) as f:
            body = f.read()
        assert "chown -R" in body, (
            "k8s_run_backup.yml no longer reclaims repo ownership; the two "
            "orchestrators have diverged again"
        )
