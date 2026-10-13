"""delete removes the code dev mode extracted, root-owned files included.

Dev mode bind-mounts mediawiki-code/ over the web container's code, so
files the container writes there are owned by root, and the operator's
own `find -delete` cannot remove them. The directory outlives
`devmode disable`, so delete removes it from inside a container whatever
the instance's current mode.
"""

import os
import stat
import subprocess
import sys
import textwrap

import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DESTROY = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks", "destroy.yml")
DELETE = os.path.join(REPO_ROOT, "roles", "delete", "tasks", "main.yml")
COMMON = os.path.join(REPO_ROOT, "roles", "common")

# Stands in for docker: `run -v HOST:/instance IMAGE CMD...` runs CMD on
# the host with /instance mapped to HOST, after granting the operator the
# write access a root container would have.
STUB = textwrap.dedent("""\
    #!{python}
    import os, subprocess, sys
    args = sys.argv[1:]
    with open({log!r}, "a") as f:
        f.write(" ".join(args) + "\\n")
    if args[:1] != ["run"]:
        sys.exit(0)
    host = args[args.index("-v") + 1].rsplit(":/instance", 1)[0]
    cmd = args[args.index("docker.io/library/alpine") + 1:]
    for root, dirs, _ in os.walk(host):
        for d in dirs:
            os.chmod(os.path.join(root, d), 0o755)
    cmd = [a.replace("/instance", host) for a in cmd]
    sys.exit(subprocess.run(cmd).returncode)
    """)


def _instance(tmp_path):
    inst = tmp_path / "inst"
    vendor = inst / "mediawiki-code" / "vendor" / "pkg"
    vendor.mkdir(parents=True)
    (vendor / "autoload.php").write_text("<?php\n")
    (inst / "config").mkdir()
    (inst / "docker-compose.yml").write_text("services: {}\n")
    # Unwritable to the operator, as a root-owned directory would be.
    vendor.chmod(stat.S_IRUSR | stat.S_IXUSR)
    vendor.parent.chmod(stat.S_IRUSR | stat.S_IXUSR)
    return inst


def _unlock(inst):
    for root, dirs, _ in os.walk(inst):
        for d in dirs:
            os.chmod(os.path.join(root, d), 0o755)


def _play(tmp_path, tasks, extra_vars):
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": dict({"canasta_root": REPO_ROOT,
                      "ansible_python_interpreter": sys.executable},
                     **extra_vars),
        "tasks": tasks,
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_FILTER_PLUGINS=os.path.join(REPO_ROOT, "filter_plugins"),
               ANSIBLE_LIBRARY=os.path.join(COMMON, "library"),
               ANSIBLE_MODULE_UTILS=os.path.join(COMMON, "module_utils"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    return subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env,
                          cwd=tmp_path)


class TestDestroyRemovesDevModeCode:
    def _destroy(self, tmp_path, inst):
        log = tmp_path / "docker.log"
        stub = tmp_path / "docker"
        stub.write_text(STUB.format(python=sys.executable, log=str(log)))
        stub.chmod(0o755)
        proc = _play(tmp_path, [{"ansible.builtin.include_tasks": DESTROY}], {
            "instance_path": str(inst),
            "instance_id": "inst",
            "instance_is_k8s": False,
            "compose_command": str(stub) + " compose",
            "inspect_command": str(stub),
        })
        return proc, log

    def test_root_owned_code_is_removed(self, tmp_path):
        inst = _instance(tmp_path)
        try:
            proc, log = self._destroy(tmp_path, inst)
            assert proc.returncode == 0, proc.stdout + proc.stderr
            assert not (inst / "mediawiki-code").exists(), (
                "mediawiki-code/ survived; the operator cannot remove it")
            assert "rm -rf /instance/mediawiki-code" in log.read_text()
        finally:
            _unlock(inst)

    def test_no_container_run_without_dev_mode_code(self, tmp_path):
        inst = tmp_path / "inst"
        (inst / "config").mkdir(parents=True)
        proc, log = self._destroy(tmp_path, inst)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "mediawiki-code" not in log.read_text()


class TestLeftoverWarning:
    def _tasks(self):
        with open(DELETE) as f:
            top = yaml.safe_load(f)
        return [t for t in top if t.get("name") in (
            "List leftovers owned by another user",
            "Warn if directory could not be removed")]

    def _warn(self, tmp_path, foreign, unshare=""):
        tasks = self._tasks()
        assert len(tasks) == 2
        proc = _play(tmp_path, tasks[1:], {
            "instance_path": "/srv/canasta/inst",
            "instance_id": "inst",
            "inspect_command": "docker",
            "_delete_unshare": unshare,
            "_rmdir": {"failed": True},
            "_delete_foreign": {"stdout_lines": foreign},
        })
        assert proc.returncode == 0, proc.stdout + proc.stderr
        return " ".join(proc.stdout.split())

    def test_names_foreign_paths_and_removal_command(self, tmp_path):
        out = self._warn(tmp_path, ["/srv/canasta/inst/mediawiki-code"])
        assert "/srv/canasta/inst/mediawiki-code" in out
        assert ("docker run --rm -v /srv/canasta/inst:/instance "
                "docker.io/library/alpine find /instance -mindepth 1 "
                "-delete") in out
        assert "current working directory" not in out

    def test_rootless_podman_uses_unshare(self, tmp_path):
        out = self._warn(tmp_path, ["/srv/canasta/inst/images"],
                         unshare="podman unshare ")
        assert "podman unshare rm -rf /srv/canasta/inst" in out

    def test_no_foreign_paths_points_at_working_directory(self, tmp_path):
        out = self._warn(tmp_path, [])
        assert "current working directory" in out
        assert "owned by another user (" not in out

    def test_lists_paths_the_operator_does_not_own(self, tmp_path):
        inst = tmp_path / "inst"
        (inst / "mine").mkdir(parents=True)
        tasks = self._tasks()[:1]
        tasks.append({"ansible.builtin.debug": {
            "var": "_delete_foreign.stdout_lines"}})
        proc = _play(tmp_path, tasks, {
            "instance_path": str(inst), "_rmdir": {"failed": True}})
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "mine" not in proc.stdout
