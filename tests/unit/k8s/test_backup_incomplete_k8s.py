"""The Kubernetes backup Job records dump failures and tags the snapshot.

The dump-databases init container dumps every database group, recording
each failure in a dump-status volume outside the snapshot; the restic step
then tags the snapshot INCOMPLETE plus missing-db:<name>, and the failures
are read back from the Job log.
"""

import os
import subprocess

import test_backup_incomplete
from test_backup_incomplete import TASKS, _by_name, _names, _render


K8S_BACKUP = os.path.join(TASKS, "k8s_run_backup.yml")


fake_mariadb = test_backup_incomplete.fake_mariadb


class TestKubernetesDump:
    def _init_script(self):
        task = _by_name(K8S_BACKUP, "Build init containers for backup ops")
        for container in task["ansible.builtin.set_fact"][
                "_backup_init_containers"]:
            if container["name"] == "dump-databases":
                return container
        raise AssertionError("dump-databases init container not found")

    def test_failures_reach_restic_outside_the_snapshot(self):
        mounts = self._init_script()["volumeMounts"]
        assert {"name": "dump-status", "mountPath": "/dump-status"} in mounts
        volumes = _by_name(
            K8S_BACKUP, "Add dumps volume + mount-on-mount for backup ops")
        facts = volumes["ansible.builtin.set_fact"]
        assert "'dump-status', 'emptyDir'" in facts["_restic_volumes"]
        assert "'mountPath': '/dump-status'" in facts["_restic_volume_mounts"]

    def test_a_failed_group_does_not_stop_the_others(
            self, tmp_path, fake_mariadb):
        dumps = tmp_path / "dumps"
        status = tmp_path / "status"
        dumps.mkdir()
        status.mkdir()
        script = _render(self._init_script()["command"][-1],
                         _backup_groups_encoded="one one_cargo;two")
        script = script.replace(
            "/currentsnapshot/config/backup", str(dumps)).replace(
            "/dump-status", str(status))
        result = subprocess.run(["sh", "-c", script], capture_output=True,
                                text=True, env=fake_mariadb)
        assert result.returncode == 0, result.stderr
        assert sorted(os.listdir(dumps)) == [
            "db_loose.sql", "db_one.sql", "db_two.sql"]
        one = (dumps / "db_one.sql").read_text()
        assert "--databases one" in one and "partial" not in one
        failures = (status / "failures").read_text().splitlines()
        assert len(failures) == 1
        assert failures[0].startswith("DUMP-FAILED one_cargo: ")

    def _tag_command(self, tmp_path, failures):
        status = tmp_path / "status"
        status.mkdir(exist_ok=True)
        if failures:
            (status / "failures").write_text(failures)
        task = _by_name(K8S_BACKUP,
                        "Tag the snapshot INCOMPLETE when a dump failed")
        assert task["when"] == "_is_backup_op | bool"
        cmd = _render(task["ansible.builtin.set_fact"]["_restic_command"],
                      _restic_command="echo restic backup /currentsnapshot")
        result = subprocess.run(
            ["sh", "-c", cmd.replace("/dump-status", str(status))],
            capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        return result.stdout.splitlines()

    def test_snapshot_is_tagged_incomplete(self, tmp_path):
        out = self._tag_command(
            tmp_path, "DUMP-FAILED one one_cargo: boom: (1356)\n"
                      "DUMP-FAILED loose: dump is empty\n")
        assert out[0] == "DUMP-FAILED one one_cargo: boom: (1356)"
        assert out[-1] == (
            "restic backup /currentsnapshot --tag INCOMPLETE,"
            "missing-db:one,missing-db:one_cargo,missing-db:loose")

    def test_complete_snapshot_is_untagged(self, tmp_path):
        assert self._tag_command(tmp_path, "") == [
            "restic backup /currentsnapshot"]

    def test_tagging_precedes_the_ownership_wrapper(self):
        # The wrapper captures restic's exit code with $?, so restic must
        # stay the last command before it.
        names = _names(K8S_BACKUP)
        assert names.index("Tag the snapshot INCOMPLETE when a dump failed") \
            < names.index("Reclaim local-path repo ownership after the op")

    def test_failures_are_read_back_from_the_job_log(self):
        task = _by_name(K8S_BACKUP, "Collect the database dumps that failed")
        expr = task["ansible.builtin.set_fact"]["_backup_dump_failures"]
        assert "_restic_log.stdout" in expr
        assert "^DUMP-FAILED " in expr
