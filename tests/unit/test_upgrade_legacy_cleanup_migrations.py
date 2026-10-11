"""Legacy cleanups that run once, on upgrade, rather than on every command.

Instances cloned from Canasta-DockerCompose carry extensions/, skins/ and
config/ .gitignore files that hide user content from gitops, and older
Kubernetes instances were scheduled by an in-cluster backup CronJob that
the crontab schedule replaced. Both are removed by upgrade migrations;
gitops init/join and backup schedule set/remove no longer carry the
cleanup themselves.
"""

import os
import subprocess
import sys

import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MIGRATIONS = os.path.join(REPO_ROOT, "roles", "upgrade", "tasks", "migrations")
UPGRADE_MAIN = os.path.join(REPO_ROOT, "roles", "upgrade", "tasks", "main.yml")
PROBE = os.path.join(REPO_ROOT, "roles", "upgrade", "files",
                     "probe_migration_state.py")
GITIGNORE_MIGRATION = os.path.join(MIGRATIONS,
                                   "remove_legacy_gitignore_files.yml")
CRONJOB_MIGRATION = os.path.join(MIGRATIONS,
                                 "remove_legacy_backup_cronjob.yml")
LEGACY = ("extensions", "skins", "config")


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _probe(inst):
    out = subprocess.run([sys.executable, PROBE, str(inst)],
                         capture_output=True, text=True, check=True).stdout
    return yaml.safe_load(out)


def _instance(tmp_path, gitops=False):
    inst = tmp_path / "inst"
    for d in LEGACY:
        (inst / d).mkdir(parents=True)
        (inst / d / ".gitignore").write_text("*\n")
    if gitops:
        (inst / ".gitops-host").write_text("host1\n")
    return inst


def _run_gitignore_migration(tmp_path, inst):
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {"instance_path": str(inst),
                 "_mig": _probe(inst),
                 "ansible_python_interpreter": sys.executable},
        "tasks": [{"ansible.builtin.include_tasks": GITIGNORE_MIGRATION}],
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0")
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    return subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env,
                          cwd=tmp_path)


class TestProbe:
    def test_flags_any_legacy_gitignore(self, tmp_path):
        inst = tmp_path / "inst"
        (inst / "skins").mkdir(parents=True)
        assert _probe(inst)["files"]["legacy_gitignore"] is False
        (inst / "skins" / ".gitignore").write_text("*\n")
        assert _probe(inst)["files"]["legacy_gitignore"] is True


class TestGitignoreMigration:
    def test_removes_the_legacy_files(self, tmp_path):
        inst = _instance(tmp_path)
        proc = _run_gitignore_migration(tmp_path, inst)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        for d in LEGACY:
            assert not (inst / d / ".gitignore").exists()

    def test_leaves_a_gitops_instance_alone(self, tmp_path):
        # Once gitops owns the tree, a .gitignore there is the operator's.
        inst = _instance(tmp_path, gitops=True)
        proc = _run_gitignore_migration(tmp_path, inst)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        for d in LEGACY:
            assert (inst / d / ".gitignore").exists()


class TestCronJobMigration:
    def test_targets_the_legacy_cronjob_on_kubernetes_only(self):
        task = _load(CRONJOB_MIGRATION)[0]
        spec = task["kubernetes.core.k8s"]
        assert spec["state"] == "absent"
        assert spec["kind"] == "CronJob"
        assert spec["name"] == "canasta-backup-{{ instance_id }}"
        assert spec["namespace"] == "canasta-{{ instance_id }}"
        assert task["when"] == "instance_is_k8s | bool"
        assert task["ignore_errors"] is True


class TestWiring:
    def test_both_run_on_upgrade(self):
        loop = next(t for t in _load(UPGRADE_MAIN)
                    if t.get("name") == "Run migrations")["loop"]
        assert "remove_legacy_gitignore_files.yml" in loop
        assert "remove_legacy_backup_cronjob.yml" in loop

    def test_no_longer_inline_elsewhere(self):
        inline = {
            "roles/gitops/tasks/init_compose.yml": "skins/.gitignore",
            "roles/gitops/tasks/join.yml": "skins/.gitignore",
            "roles/orchestrator/tasks/backup_schedule_apply.yml": "CronJob",
            "roles/orchestrator/tasks/backup_schedule_unapply.yml": "CronJob",
        }
        for rel, marker in inline.items():
            with open(os.path.join(REPO_ROOT, rel)) as f:
                assert marker not in f.read(), rel
