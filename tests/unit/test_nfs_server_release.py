"""Uninstalling k8s stops the NFS server that storage setup started.

`canasta storage setup nfs --install-server` installs and starts
nfs-server. Uninstalling k8s withdrew its export but left the server
running with nothing to serve. Storage setup now records when it started
a server that was not running, and once uninstall has withdrawn the
exports it stops and disables that server if the host exports nothing
else. A server that was running before, or that still exports other
shares, is left running and the report says why.

nfs_server_release.yml runs on localhost with exportfs and systemctl
stubbed.
"""

import os
import subprocess
import sys

import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ORCH_TASKS = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks")
RELEASE = os.path.join(ORCH_TASKS, "nfs_server_release.yml")
REMOVE = os.path.join(ORCH_TASKS, "nfs_exports_remove.yml")
SETUP = os.path.join(REPO_ROOT, "playbooks", "storage_setup_nfs.yml")
MARKER = "/etc/canasta/nfs-server-started"


def _stub(bindir, name, body):
    path = bindir / name
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)


def _run(tmp_path, marker, exports):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "systemctl.log"
    _stub(bindir, "systemctl",
          f'echo "$@" >> {log}\n'
          '[ "$1" = is-active ] && echo active\nexit 0\n')
    _stub(bindir, "exportfs", f"printf '%s' '{exports}'\n")
    marker_path = tmp_path / "nfs-server-started"
    if marker:
        marker_path.write_text("started\n")
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {"_nfs_server_marker": str(marker_path),
                 "ansible_become": False,
                 "ansible_python_interpreter": sys.executable},
        "tasks": [{"ansible.builtin.include_tasks": RELEASE}],
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               PATH=f"{bindir}:{os.environ['PATH']}")
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env,
                          cwd=tmp_path)
    calls = log.read_text() if log.exists() else ""
    return proc, calls, marker_path


class TestRelease:
    def test_last_export_gone_stops_the_server_setup_started(self, tmp_path):
        proc, calls, marker = _run(tmp_path, marker=True, exports="")
        out = proc.stdout + proc.stderr
        assert proc.returncode == 0, out
        assert "disable --now nfs-server" in calls
        assert not marker.exists()
        assert "Stopped and disabled nfs-server" in out

    def test_other_exports_keep_it_running(self, tmp_path):
        proc, calls, marker = _run(
            tmp_path, marker=True,
            exports="/srv/other\t10.0.0.0/8(rw)\n")
        out = proc.stdout + proc.stderr
        assert proc.returncode == 0, out
        assert "disable" not in calls
        assert marker.exists()
        assert "Left nfs-server running: it still exports /srv/other" in out

    def test_a_server_running_before_setup_is_left_alone(self, tmp_path):
        proc, calls, _ = _run(tmp_path, marker=False, exports="")
        out = proc.stdout + proc.stderr
        assert proc.returncode == 0, out
        assert "disable" not in calls
        assert ("Left nfs-server running: it was running before "
                "canasta storage setup nfs") in out


def _tasks(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _walk(tasks):
    for t in tasks:
        yield t
        yield from _walk(t.get("block", []))


class TestWiring:
    def test_withdrawal_releases_the_server(self):
        inc = [t for t in _walk(_tasks(REMOVE))
               if "nfs_server_release.yml" in str(
                   t.get("ansible.builtin.include_tasks", ""))]
        assert inc and inc[0]["when"] == (
            "not _nfs_rm_kubeconfig_stat.stat.exists")

    def test_setup_records_only_a_server_it_started(self):
        tasks = list(_walk(_tasks(SETUP)))
        names = [t.get("name") for t in tasks]
        check = names.index("Check whether the NFS server is already running")
        assert check < names.index("Ensure NFS server is running")
        assert check < names.index("Install NFS server packages")
        record = tasks[names.index(
            "Record that storage setup started the NFS server")]
        assert MARKER in record["ansible.builtin.copy"]["dest"]
        assert any("_nfs_server_was_active.stdout" in w and "'active'" in w
                   for w in record["when"])

    def test_both_sides_use_the_same_marker(self):
        release = _tasks(RELEASE)
        assert MARKER in release[0]["ansible.builtin.stat"]["path"]
