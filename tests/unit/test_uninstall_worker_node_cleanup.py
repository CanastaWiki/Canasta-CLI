"""Removing a worker's Node from the control plane after its k3s is gone.

With --force and an unreachable control plane, the worker uninstall used
to remove k3s and then exit with the raw ssh error from the Node removal.
A re-run then reported "k3s is not installed." and never removed the
Node. Now --force turns the failed removal into a warning naming the
command to run on the control plane, and a re-run with --cp-host still
removes the Node.

These run uninstall_k3s.yml on localhost with ssh and hostname stubbed.
The host has no k3s uninstall script, so the k3s removal itself is
skipped; the worker case sets the role the script check would have set.
"""

import os
import subprocess
import sys

import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
UNINSTALL = os.path.join(REPO_ROOT, "roles", "install", "tasks",
                         "uninstall_k3s.yml")
COMMON = os.path.join(REPO_ROOT, "roles", "common")


def _stub(bindir, name, body):
    path = bindir / name
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)


def _run(tmp_path, ssh_ok, force, worker, cp_host="cp"):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "ssh.log"
    _stub(bindir, "hostname", "echo worker1\n")
    if ssh_ok:
        _stub(bindir, "ssh", f'echo "$@" >> {log}\n'
              'case "$*" in *"delete node"*) echo \'node "worker1" deleted\';; esac\n')
    else:
        # ssh_ok=None: reachable for the volume check, gone for the delete.
        guard = 'case "$*" in *"delete node"*) ;; *) exit 0;; esac\n' \
            if ssh_ok is None else ""
        _stub(bindir, "ssh", f'echo "$@" >> {log}\n' + guard +
              'echo "ssh: connect to host 10.0.0.1 port 22: '
              'Network is unreachable" >&2\nexit 255\n')
    conf = tmp_path / "conf"
    conf.mkdir()
    (conf / "hosts.yml").write_text(yaml.safe_dump({"all": {"hosts": {
        "cp": {"ansible_host": "10.0.0.1", "ansible_user": "admin"}}}}))
    home = tmp_path / "home"
    home.mkdir()
    inventory = tmp_path / "hosts"
    inventory.write_text(
        "localhost ansible_connection=local\n"
        "canasta_controller ansible_connection=local\n")
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "gather_facts": False,
        "vars": {"canasta_root": REPO_ROOT, "cp_host": cp_host, "force": force,
                 "k3s_leftover_paths": [str(tmp_path / "rancher-node"),
                                        str(tmp_path / "rancher-cache")],
                 "ansible_python_interpreter": sys.executable},
        "tasks": [{"ansible.builtin.include_tasks": UNINSTALL}],
    }]))
    extra = ["-e", "ansible_become=false"]
    if worker:
        extra += ["-e", "_k3s_role_label=worker"]
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               HOME=str(home), CANASTA_CONFIG_DIR=str(conf),
               PATH=f"{bindir}:{os.environ['PATH']}",
               ANSIBLE_LIBRARY=os.path.join(COMMON, "library"),
               ANSIBLE_MODULE_UTILS=os.path.join(COMMON, "module_utils"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", str(inventory), str(play), *extra],
                          capture_output=True, text=True, env=env,
                          cwd=tmp_path)
    calls = log.read_text() if log.exists() else ""
    return proc, calls


class TestUnreachableControlPlane:
    def test_force_warns_with_the_command_to_run_later(self, tmp_path):
        proc, calls = _run(tmp_path, ssh_ok=False, force=True, worker=True)
        out = proc.stdout + proc.stderr
        assert proc.returncode == 0, out
        assert "could not remove Node 'worker1'" in out
        assert "sudo k3s kubectl delete node worker1" in out
        assert "Network is unreachable" in out
        assert "delete node worker1" in calls

    def test_without_force_it_fails_with_the_same_advice(self, tmp_path):
        proc, _ = _run(tmp_path, ssh_ok=None, force=False, worker=True)
        out = proc.stdout + proc.stderr
        assert proc.returncode != 0
        assert "Could not remove Node 'worker1'" in out
        assert "sudo k3s kubectl delete node worker1" in out
        assert "non-zero return code" not in out


class TestRerunAfterK3sIsGone:
    def test_it_still_removes_the_node(self, tmp_path):
        proc, calls = _run(tmp_path, ssh_ok=True, force=False, worker=False)
        out = proc.stdout + proc.stderr
        assert proc.returncode == 0, out
        assert "k3s is not installed." in out
        assert "admin@10.0.0.1" in calls
        assert "delete node worker1 --ignore-not-found" in calls
        assert "Removed Node 'worker1' from control plane." in out

    def test_node_leftovers_are_removed(self, tmp_path):
        leftover = tmp_path / "rancher-node"
        leftover.mkdir()
        (leftover / "password").write_text("pw\n")
        proc, _ = _run(tmp_path, ssh_ok=True, force=False, worker=False)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert not leftover.exists()
        assert "/etc/rancher" not in proc.stdout
        assert "/var/lib/rancher" not in proc.stdout

    def test_without_cp_host_nothing_is_attempted(self, tmp_path):
        proc, calls = _run(tmp_path, ssh_ok=True, force=False, worker=False,
                           cp_host="")
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert calls == ""
