"""The backup-schedule sweep removes every crontab entry for one instance,
whichever marker wrote it, and delete uses it.

delete used to remove the entry with the cron module, which matches only
its own '#Ansible:' marker: the containerized CLI's '# BEGIN/END' block
survived, and cron kept backing up an instance that no longer existed.
"""

import os
import shlex
import stat
import subprocess

import jinja2
import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
SWEEP = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "backup_schedule_sweep.yml")
DELETE = os.path.join(REPO_ROOT, "roles", "delete", "tasks", "main.yml")

LIVE = "Remove any prior backup schedule entries for this instance (live crontab)"
FILE = "Remove any prior backup schedule entries for this instance (host crontab file)"

CRONTAB = """\
MAILTO=""
# BEGIN canasta-backup-mysite
0 2 * * * { /usr/local/bin/canasta backup create -i mysite --tag scheduled ; } >> "/s/mysite/backup.log" 2>&1
# END canasta-backup-mysite
#Ansible: canasta-backup-mysite
0 3 * * * { canasta backup create -i mysite --tag scheduled ; } >> "/s/mysite/backup.log" 2>&1
# BEGIN canasta-backup-mysite2
0 4 * * * { canasta backup create -i mysite2 --tag scheduled ; } >> "/s/mysite2/backup.log" 2>&1
# END canasta-backup-mysite2
15 * * * * /usr/bin/true
"""

KEPT = """\
MAILTO=""
# BEGIN canasta-backup-mysite2
0 4 * * * { canasta backup create -i mysite2 --tag scheduled ; } >> "/s/mysite2/backup.log" 2>&1
# END canasta-backup-mysite2
15 * * * * /usr/bin/true
"""


def _tasks(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _by_name(path, name):
    return next(t for t in _tasks(path)
                if isinstance(t, dict) and t.get("name") == name)


def _render(task_name, **variables):
    env = jinja2.Environment()
    env.filters["quote"] = lambda s: shlex.quote(str(s))
    cmd = _by_name(SWEEP, task_name)["ansible.builtin.shell"]["cmd"]
    # The task uses a '>-' folded scalar, which YAML has already joined.
    return env.from_string(cmd).render(**variables)


def _run(cmd, env=None):
    return subprocess.run(["/bin/bash", "-c", cmd], capture_output=True,
                          text=True, env=env, check=True)


class TestHostCrontabFile:
    def test_removes_every_entry_for_the_instance_only(self, tmp_path):
        f = tmp_path / "host-crontab"
        f.write_text(CRONTAB)
        out = _run(_render(FILE, instance_id="mysite",
                           _host_crontab_file=str(f)))
        assert f.read_text() == KEPT
        assert "swept" in out.stdout

    def test_leaves_a_crontab_without_entries_byte_identical(self, tmp_path):
        # The wrapper installs the file only when its checksum changed.
        f = tmp_path / "host-crontab"
        for content in ("", KEPT, KEPT + "\n\n"):
            f.write_text(content)
            out = _run(_render(FILE, instance_id="mysite",
                               _host_crontab_file=str(f)))
            assert f.read_text() == content
            assert "swept" not in out.stdout

    def test_missing_file_is_a_noop(self, tmp_path):
        _run(_render(FILE, instance_id="mysite",
                     _host_crontab_file=str(tmp_path / "absent")))


class TestLiveCrontab:
    def _fake_crontab(self, tmp_path, content):
        store = tmp_path / "store"
        store.write_text(content)
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        script = bin_dir / "crontab"
        script.write_text(
            "#!/bin/bash\n"
            'if [ "$1" = -l ]; then cat %s; else cat > %s; fi\n'
            % (shlex.quote(str(store)), shlex.quote(str(store))))
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        env = dict(os.environ, PATH="%s:/usr/bin:/bin" % bin_dir)
        return store, env

    def test_removes_every_entry_for_the_instance_only(self, tmp_path):
        store, env = self._fake_crontab(tmp_path, CRONTAB)
        _run(_render(LIVE, instance_id="mysite"), env=env)
        assert store.read_text() == KEPT

    def test_does_not_rewrite_without_entries(self, tmp_path):
        store, env = self._fake_crontab(tmp_path, KEPT + "\n")
        out = _run(_render(LIVE, instance_id="mysite"), env=env)
        assert store.read_text() == KEPT + "\n"
        assert "swept" not in out.stdout

    def test_host_without_crontab_is_a_noop(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        # PATH holds nothing, so `command -v crontab` finds no binary and
        # the sweep exits before running anything else.
        out = _run(_render(LIVE, instance_id="mysite"),
                   env=dict(os.environ, PATH=str(empty)))
        assert out.stdout == ""


class TestDeleteUsesTheSweep:
    def test_delete_includes_the_sweep_after_teardown(self):
        tasks = _tasks(DELETE)
        names = [t.get("name") for t in tasks]
        sweep = _by_name(DELETE, "Remove backup schedule")
        assert "backup_schedule_sweep.yml" in sweep["ansible.builtin.include_tasks"]
        assert names.index("Destroy containers and volumes") \
            < names.index("Remove backup schedule")
        assert not any("ansible.builtin.cron" in t for t in tasks)

    def test_delete_edits_the_host_file_only_for_local_runs(self):
        t = _by_name(DELETE, "Select crontab target for the backup schedule")
        use_file = t["ansible.builtin.set_fact"]["_sched_use_file"]
        assert "CANASTA_HOST_CRONTAB" in use_file
        assert "target_host is not defined" in use_file
        assert "_instance_host" in use_file
