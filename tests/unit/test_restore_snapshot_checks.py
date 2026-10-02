"""A restore that cannot proceed fails cleanly and leaves no marker.

- An explicit snapshot ID is checked against the repository listing before
  anything is touched, so an unknown or ambiguous ID fails up front.
- A failed Compose restic run reports restic's own error, not the raw
  `docker run` command line.
- A restore that fails while still in its "starting" phase has changed
  nothing, so it removes its marker instead of leaving one that the next
  restore and `canasta list` read as an interrupted restore. Each
  orchestrator marks the "files" phase right before its first change.
"""

import os

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
BACKUP = os.path.join(REPO_ROOT, "roles", "backup", "tasks")
ORCH = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks")
RESTORE = os.path.join(BACKUP, "restore.yml")
RESTORE_K8S = os.path.join(BACKUP, "restore_k8s.yml")
RESTORE_INSTANCE = os.path.join(ORCH, "restore_instance.yml")
RUN_BACKUP = os.path.join(ORCH, "run_backup.yml")


def _walk(tasks):
    for t in tasks or []:
        if not isinstance(t, dict):
            continue
        yield t
        for key in ("block", "rescue", "always"):
            yield from _walk(t.get(key))


def _tasks(path):
    with open(path) as f:
        return list(_walk(yaml.safe_load(f)))


def _names(path):
    return [t.get("name") for t in _tasks(path)]


def _by_name(path, name):
    return next(t for t in _tasks(path) if t.get("name") == name)


def _render(expr, **variables):
    templar = Templar(loader=DataLoader(), variables=variables)
    return templar.template(trust_as_template(expr))


SNAPSHOTS = [
    {"id": "aa11bb22cc33", "short_id": "aa11bb22"},
    {"id": "aa11ff00ee99", "short_id": "aa11ff00"},
    {"id": "deadbe11ffff", "short_id": "deadbe11"},
]


class TestSnapshotIdIsCheckedUpFront:
    def _check(self, snapshot):
        find = _by_name(RESTORE, "Find the snapshot in the repository")
        matches = _render(
            find["ansible.builtin.set_fact"]["_restore_matches"],
            _restore_snapshots=SNAPSHOTS, _resolved_snapshot=snapshot)
        fail = _by_name(RESTORE, "Fail when the snapshot is not in the repository")
        variables = {"_restore_matches": matches,
                     "_resolved_snapshot": snapshot, "instance_id": "mywiki"}
        fails = _render("{{ %s }}" % fail["when"], **variables)
        msg = _render(fail["ansible.builtin.fail"]["msg"], **variables)
        return fails, msg

    def test_unknown_id_is_refused(self):
        fails, msg = self._check("deadbeef")
        assert fails
        assert "Snapshot 'deadbeef' not found in the backup repository." in msg
        assert "canasta backup list -i mywiki" in msg

    def test_ambiguous_prefix_is_refused(self):
        fails, msg = self._check("aa11")
        assert fails
        assert "matches more than one snapshot (aa11bb22, aa11ff00)" in msg

    def test_unique_prefix_passes(self):
        fails, _ = self._check("deadbe")
        assert not fails

    def test_checked_before_the_marker_is_written(self):
        names = _names(RESTORE)
        assert (names.index("Set effective snapshot ID")
                < names.index("Fail when the snapshot is not in the repository")
                < names.index("Mark the restore as in flight"))


class TestFailedRestoreClearsANoChangeMarker:
    def _block(self):
        with open(RESTORE) as f:
            top = yaml.safe_load(f)
        return next(t for t in top if t.get("name") == "Restore from snapshot")

    def test_orchestrator_restore_runs_inside_the_rescued_block(self):
        block = self._block()
        assert [t["name"] for t in block["block"]] == ["Run the orchestrator restore"]

    def test_marker_cleared_only_in_the_starting_phase(self):
        clear = next(t for t in _walk(self._block()["rescue"])
                     if t.get("name") == "Clear the marker of a restore that changed nothing")
        for phase, cleared in (("starting", True), ("files", False),
                               ("databases", False)):
            content = "x: y\nphase: %s\nstarted: z\n" % phase
            import base64
            result = _render(
                "{{ %s }}" % clear["when"],
                _restore_marker_at_failure={
                    "content": base64.b64encode(content.encode()).decode()})
            assert result is cleared, phase

    def test_the_failure_is_reported_with_the_instance_state(self):
        import base64
        report = self._block()["rescue"][-1]
        assert "ansible.builtin.fail" in report
        for phase, expected in (
                ("starting", "before changing anything"),
                ("files", "partway through, in its files phase"),
                (None, "The restore failed (see the error above).")):
            marker = ({"content": base64.b64encode(
                ("phase: %s\n" % phase).encode()).decode()} if phase else {})
            phase_now = _render(report["vars"]["_restore_failed_phase"],
                                _restore_marker_at_failure=marker)
            msg = _render(report["ansible.builtin.fail"]["msg"],
                          _restore_failed_phase=phase_now)
            assert expected in msg, (phase, msg)


class TestFilesPhaseIsMarkedBeforeTheFirstChange:
    def test_compose_checks_the_wiki_before_marking(self):
        names = _names(RESTORE_INSTANCE)
        mark = names.index("Mark the files phase")
        assert names.index("Fail when the target wiki is absent from the snapshot") < mark
        assert mark < names.index("Copy files from volume to host")

    def test_kubernetes_marks_before_the_restore_pod(self):
        names = _names(RESTORE_K8S)
        assert names.index("Mark the files phase") < names.index("Create restore pod")


class TestComposeResticFailureIsReadable:
    def test_restic_failure_reported_by_its_own_task(self):
        run = _by_name(RUN_BACKUP, "Run restic container")
        assert run.get("failed_when") is False
        fail = _by_name(RUN_BACKUP, "Fail when restic fails")
        assert fail["when"] == "_compose_restic_result.rc != 0"
        names = _names(RUN_BACKUP)
        assert names.index("Run restic container") + 1 == names.index("Fail when restic fails")

    def test_message_names_the_command_and_restic_error(self):
        fail = _by_name(RUN_BACKUP, "Fail when restic fails")
        variables = {k: trust_as_template(v) if isinstance(v, str) else v
                     for k, v in fail["vars"].items()}
        msg = _render(
            fail["ansible.builtin.fail"]["msg"],
            backup_args=["restore", "deadbeef", "--target", "/"],
            **variables,
            _compose_restic_result={
                "rc": 1,
                "stderr": 'Fatal: failed to find snapshot: no matching ID found for prefix "deadbeef"\n'})
        assert msg.startswith("restic restore failed (exit 1).")
        assert "Fatal: failed to find snapshot" in msg
        assert "docker run" not in msg
