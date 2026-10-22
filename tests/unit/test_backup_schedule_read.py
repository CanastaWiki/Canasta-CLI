"""config/backup-schedule.yml in the single-duration form is rewritten.

Before the retention policy became a mapping, the file carried one
purge_older_than duration, meaning --keep-within. Upgrade, restore and
gitops pull all read the file through backup_schedule_read.yml, which
rewrites that form as a retention policy and stages it for gitops, so no
reader has to understand the old key.
"""

import json
import os
import subprocess
import sys

import pytest
import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
READ = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks",
                    "backup_schedule_read.yml")
REMATERIALIZE = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks",
                             "backup_schedule_rematerialize.yml")
UPGRADE_MAIN = os.path.join(REPO_ROOT, "roles", "upgrade", "tasks", "main.yml")
MIGRATION = os.path.join(REPO_ROOT, "roles", "upgrade", "tasks", "migrations",
                         "convert_backup_schedule_retention.yml")
PROBE = os.path.join(REPO_ROOT, "roles", "upgrade", "files",
                     "probe_migration_state.py")


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                          text=True, check=True).stdout


def _instance(tmp_path, schedule, gitops=True):
    inst = tmp_path / "inst"
    (inst / "config").mkdir(parents=True)
    if schedule is not None:
        (inst / "config" / "backup-schedule.yml").write_text(schedule)
    if gitops:
        _git(inst, "init", "-q")
        _git(inst, "-c", "user.email=t@example.com", "-c", "user.name=t",
             "commit", "-q", "--allow-empty", "-m", "init")
        if schedule is not None:
            _git(inst, "add", "config/backup-schedule.yml")
            _git(inst, "-c", "user.email=t@example.com", "-c", "user.name=t",
                 "commit", "-q", "-m", "schedule")
        (inst / ".gitops-host").write_text("prod\n")
    return inst


def _read(tmp_path, inst):
    result = tmp_path / "result.json"
    play = [{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {"canasta_root": REPO_ROOT, "instance_path": str(inst)},
        "tasks": [
            {"ansible.builtin.include_tasks": READ},
            {"ansible.builtin.copy": {
                "content": "{{ _sched_state_data | to_json }}",
                "dest": str(result),
                "mode": "0644",
            }},
        ],
    }]
    playfile = tmp_path / "play.yml"
    playfile.write_text(yaml.safe_dump(play, sort_keys=False))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0")
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(playfile)],
                          capture_output=True, text=True, env=env, cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return json.loads(result.read_text())


def _on_disk(inst):
    path = inst / "config" / "backup-schedule.yml"
    return yaml.safe_load(path.read_text()) if path.exists() else None


class TestRead:
    def test_a_single_duration_becomes_keep_within(self, tmp_path):
        inst = _instance(tmp_path, "cron_expression: 0 2 * * 0\n"
                                   "purge_older_than: 90d\n")
        expected = {"cron_expression": "0 2 * * 0",
                    "retention": {"keep-within": "90d"}}
        assert _read(tmp_path, inst) == expected
        assert _on_disk(inst) == expected

    def test_the_rewrite_is_staged_for_gitops(self, tmp_path):
        inst = _instance(tmp_path, "cron_expression: 0 2 * * 0\n"
                                   "purge_older_than: 90d\n")
        _read(tmp_path, inst)
        assert _git(inst, "diff", "--cached", "--name-only").split() == [
            "config/backup-schedule.yml"]

    def test_a_retention_policy_beside_the_old_key_wins(self, tmp_path):
        inst = _instance(tmp_path, "cron_expression: 0 2 * * 0\n"
                                   "purge_older_than: 90d\n"
                                   "retention:\n  keep-daily: '7'\n")
        expected = {"cron_expression": "0 2 * * 0",
                    "retention": {"keep-daily": "7"}}
        assert _read(tmp_path, inst) == expected
        assert _on_disk(inst) == expected

    def test_an_empty_duration_leaves_no_retention(self, tmp_path):
        inst = _instance(tmp_path, "cron_expression: 0 2 * * 0\n"
                                   "purge_older_than: ''\n")
        assert _read(tmp_path, inst) == {"cron_expression": "0 2 * * 0"}

    def test_the_current_form_is_left_alone(self, tmp_path):
        text = ("cron_expression: 0 2 * * 0\n"
                "retention:\n    keep-within: 90d\n")
        inst = _instance(tmp_path, text)
        assert _read(tmp_path, inst) == {"cron_expression": "0 2 * * 0",
                                         "retention": {"keep-within": "90d"}}
        assert (inst / "config" / "backup-schedule.yml").read_text() == text
        assert _git(inst, "status", "--porcelain", "config") == ""

    def test_no_file_reads_as_empty_and_creates_none(self, tmp_path):
        inst = _instance(tmp_path, None)
        assert _read(tmp_path, inst) == {}
        assert _on_disk(inst) is None

    def test_a_non_gitops_instance_is_rewritten_too(self, tmp_path):
        inst = _instance(tmp_path, "cron_expression: 0 2 * * 0\n"
                                   "purge_older_than: 30d\n", gitops=False)
        _read(tmp_path, inst)
        assert _on_disk(inst)["retention"] == {"keep-within": "30d"}


class TestWiring:
    def test_rematerialize_reads_through_the_rewrite(self):
        """Restore and gitops pull both re-materialize the crontab."""
        with open(REMATERIALIZE) as f:
            text = f.read()
        assert "backup_schedule_read.yml" in text
        assert "purge_older_than" not in text

    def test_upgrade_runs_the_migration(self):
        with open(UPGRADE_MAIN) as f:
            assert "convert_backup_schedule_retention.yml" in f.read()
        with open(MIGRATION) as f:
            text = f.read()
        assert "backup_schedule_read.yml" in text
        assert "backup_schedule_has_purge_older_than" in text

    @pytest.mark.parametrize("schedule, expected", [
        ("cron_expression: 0 2 * * 0\npurge_older_than: 90d\n", True),
        ("cron_expression: 0 2 * * 0\nretention:\n  keep-within: 90d\n", False),
        (None, False),
    ])
    def test_the_probe_flags_only_the_old_form(self, tmp_path, schedule,
                                               expected):
        inst = _instance(tmp_path, schedule, gitops=False)
        out = subprocess.run([sys.executable, PROBE, str(inst)],
                             capture_output=True, text=True, check=True).stdout
        assert json.loads(out)["files"][
            "backup_schedule_has_purge_older_than"] is expected
