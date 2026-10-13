"""Smaller fixes from a hands-on Kubernetes e2e.

Each class covers one: the run's vars file is removed, a stuck namespace is
not waited on again under --force, gitops pull only asks for a push when
there is something to push, and what backup, upgrade, add and
uninstall left behind.
"""

import json
import os
import stat
import subprocess
import sys

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
sys.path.insert(0, REPO_ROOT)

import canasta  # noqa: E402

ORCH = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks")


def _load(*parts):
    with open(os.path.join(REPO_ROOT, *parts)) as f:
        return yaml.safe_load(f)


def _from(tasks, first_name):
    """The tasks from the one named first_name to the end."""
    names = [t.get("name") for t in tasks]
    return tasks[names.index(first_name):]


def _named(tasks, name):
    return next(t for t in tasks if t.get("name") == name)


def _run(tmp_path, tasks, extra_vars=None, path_prefix=None, play_vars=None):
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": dict({"ansible_python_interpreter": sys.executable},
                     **(play_vars or {})),
        "tasks": tasks,
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0")
    if path_prefix:
        env["PATH"] = str(path_prefix) + os.pathsep + env["PATH"]
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    argv = [playbook, "-i", "localhost,", str(play)]
    if extra_vars:
        argv += ["-e", json.dumps(extra_vars)]
    return subprocess.run(argv, capture_output=True, text=True, env=env,
                          cwd=tmp_path, timeout=120)


def _fake_bin(tmp_path, name, body):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    script = bindir / name
    script.write_text("#!/bin/sh\n" + body)
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return bindir


class TestVarsFileRemoved:
    """config set values (secrets included) must not outlive the run."""

    def test_build_ansible_args_names_its_vars_file(self, tmp_path,
                                                     monkeypatch):
        monkeypatch.setenv("CANASTA_CONFIG_DIR", str(tmp_path))

        class _A:
            def __getattr__(self, name):
                return None
        args = _A()
        args.verbose = False
        argv = canasta.build_ansible_args(
            "ansible-playbook", "version", args, _load("meta",
                                                       "command_definitions.yml"))
        path = argv[argv.index("-e") + 1].lstrip("@")
        try:
            with open(path) as f:
                assert json.load(f)["canasta_vars_file"] == path
        finally:
            os.unlink(path)

    def test_canasta_yml_removes_it_once_loaded(self, tmp_path):
        pre = _load("canasta.yml")[0]["pre_tasks"]
        tasks = [_named(pre, "Add the controller as a delegation target"),
                 _named(pre, "Remove the vars file canasta.py wrote")]
        vars_file = tmp_path / "canasta-vars-test.json"
        vars_file.write_text(json.dumps({
            "canasta_vars_file": str(vars_file), "secret": "s3cret"}))
        play = tmp_path / "play.yml"
        play.write_text(yaml.safe_dump([{
            "hosts": "localhost", "connection": "local",
            "gather_facts": False,
            "vars": {"ansible_python_interpreter": sys.executable},
            "tasks": tasks + [{"ansible.builtin.debug": {
                "msg": "still have {{ secret }}"}}],
        }]))
        playbook = os.path.join(os.path.dirname(sys.executable),
                                "ansible-playbook")
        proc = subprocess.run(
            [playbook, "-i", "localhost,", str(play), "-e", "@%s" % vars_file],
            capture_output=True, text=True, cwd=tmp_path, timeout=120,
            env=dict(os.environ, ANSIBLE_NOCOLOR="1"))
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert not vars_file.exists()
        # The vars were loaded before the file went.
        assert "still have s3cret" in proc.stdout


class TestStuckNamespaceUnderForce:
    """delete --force on a namespace already stuck in Terminating."""

    def _tasks(self):
        return _from(_load("roles", "orchestrator", "tasks",
                           "helm_uninstall.yml"),
                     "Check whether the namespace is already terminating")

    def _kubectl(self, tmp_path, phase, still_there):
        log = tmp_path / "kubectl.log"
        body = (
            'echo "$*" >> %s\n'
            'case "$*" in\n'
            '  *jsonpath*) echo %s ;;\n'
            '  *"-o name"*) %s ;;\n'
            'esac\n' % (log, phase,
                        "echo namespace/canasta-demo" if still_there
                        else "exit 1"))
        return _fake_bin(tmp_path, "kubectl", body), log

    def test_force_does_not_wait_again(self, tmp_path):
        bindir, log = self._kubectl(tmp_path, "Terminating", still_there=True)
        proc = _run(tmp_path, self._tasks(), path_prefix=bindir,
                    play_vars={"_k8s_namespace": "canasta-demo",
                               "instance_id": "demo", "force": True})
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "-o name" not in log.read_text()
        assert "still terminating" in proc.stdout

    def test_without_force_it_still_waits(self, tmp_path):
        bindir, log = self._kubectl(tmp_path, "Terminating", still_there=False)
        proc = _run(tmp_path, self._tasks(), path_prefix=bindir,
                    play_vars={"_k8s_namespace": "canasta-demo",
                               "instance_id": "demo", "force": False})
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "-o name" in log.read_text()

    def test_force_waits_for_a_namespace_it_deletes(self, tmp_path):
        bindir, log = self._kubectl(tmp_path, "Active", still_there=False)
        proc = _run(tmp_path, self._tasks(), path_prefix=bindir,
                    play_vars={"_k8s_namespace": "canasta-demo",
                               "instance_id": "demo", "force": True})
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "-o name" in log.read_text()


def _git(cwd, *args):
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com",
         "-c", "commit.gpgsign=false", *args],
        cwd=cwd, check=True, capture_output=True, text=True).stdout


class TestPullPushHint:
    """gitops pull (K8s) asks for a push only when the render changed."""

    def _setup(self, tmp_path):
        origin = tmp_path / "origin.git"
        _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
        inst = tmp_path / "inst"
        _git(tmp_path, "clone", "-q", str(origin), str(inst))
        (inst / "values.template.yaml").write_text("a: 1\n")
        (inst / "hosts" / "h1").mkdir(parents=True)
        (inst / "hosts" / "h1" / "rendered-values.yaml").write_text("a: 1\n")
        _git(inst, "add", "-A")
        _git(inst, "commit", "-qm", "one")
        _git(inst, "push", "-q", "origin", "HEAD:main")
        other = tmp_path / "other"
        _git(tmp_path, "clone", "-q", str(origin), str(other))
        (other / "note").write_text("x\n")
        _git(other, "add", "note")
        _git(other, "commit", "-qm", "two")
        _git(other, "push", "-q", "origin", "HEAD:main")
        return inst

    def _tasks(self, rewrite):
        tasks = _load("roles", "gitops", "tasks", "pull_kubernetes.yml")
        render = {"name": "Render per-host values (stub)",
                  "ansible.builtin.set_fact": {"_render_hostname": "h1"}}
        out = []
        for t in tasks:
            if t.get("name") == "Render per-host values":
                out.append(render)
                if rewrite:
                    out.append({"ansible.builtin.copy": {
                        "content": "a: 2\n",
                        "dest": "{{ instance_path }}/hosts/h1/rendered-values.yaml"}})
            elif t.get("name") in ("Update submodules",
                                   "Explain and roll back a failed pull"):
                continue
            else:
                out.append(t)
        return out

    def test_no_hint_when_render_is_unchanged(self, tmp_path):
        inst = self._setup(tmp_path)
        proc = _run(tmp_path, self._tasks(rewrite=False),
                    play_vars={"instance_path": str(inst),
                               "gitops_k8s_git_env": {}})
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert (inst / "note").exists()
        assert "canasta gitops push" not in proc.stdout

    def test_hint_when_render_changed(self, tmp_path):
        inst = self._setup(tmp_path)
        proc = _run(tmp_path, self._tasks(rewrite=True),
                    play_vars={"instance_path": str(inst),
                               "gitops_k8s_git_env": {}})
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "canasta gitops push" in proc.stdout


class TestBackupMountpointRemoved:
    """The dumps overlay's mountpoint is created inside config/ as root."""

    def test_backup_job_removes_the_empty_mountpoint(self):
        tasks = _load("roles", "orchestrator", "tasks", "k8s_run_backup.yml")
        task = _named(tasks,
                      "Remove the empty dumps mountpoint from the host config dir")
        argv = task["ansible.builtin.command"]["argv"]
        # rmdir, never rm -rf: anything inside the directory stays.
        assert argv == ["rmdir", "{{ instance_path }}/config/backup"]
        assert "_is_backup_op" in str(task["when"])
        names = [t.get("name") for t in tasks]
        assert names.index(task["name"]) > names.index(
            "Read the restic Job's output and delete the Job")


class TestValuesYamlIndent:
    """add rewrites values.yaml in the indentation gitops push writes."""

    def test_add_writes_values_yaml_with_indent_2(self):
        add = _load("playbooks", "add.yml")
        block = _named(add, "Update Kubernetes values.yaml domains list")["block"]
        task = _named(block, "Update values.yaml with new domains list")
        assert "to_nice_yaml(indent=2)" in task["ansible.builtin.copy"]["content"]


class TestMycnfMigrationComposeOnly:
    """Kubernetes serves the chart's my.cnf, so there is nothing to migrate."""

    def test_upgrade_skips_it_on_kubernetes(self):
        tasks = _load("roles", "upgrade", "tasks", "main.yml")
        task = _named(tasks, "Backfill gitops my.cnf.template if missing")
        assert "not (instance_is_k8s | bool)" in str(task.get("when"))


class TestUninstallNodeLeftovers:
    """uninstall k8s removes what the k3s uninstall scripts keep."""

    def test_node_password_and_cache_are_removed(self, tmp_path):
        tasks = _load("roles", "install", "tasks", "uninstall_k3s.yml")
        check = _named(tasks, "Check for k3s node leftovers")
        assert check["loop"] == ["/etc/rancher/node", "/var/lib/rancher/cache"]
        names = [t.get("name") for t in tasks]
        # Last in the flow, after the worker's Node cleanup.
        assert names[-1] == "Remove k3s node leftovers"

    def test_removal_runs_only_once_k3s_is_gone(self, tmp_path):
        tasks = _from(_load("roles", "install", "tasks", "uninstall_k3s.yml"),
                      "Check for a k3s install still on this host")
        node = tmp_path / "node"
        cache = tmp_path / "cache"
        script = tmp_path / "k3s-uninstall.sh"
        for t in tasks:
            t.pop("become", None)
            if t.get("loop") == ["/etc/rancher/node", "/var/lib/rancher/cache"]:
                t["loop"] = [str(node), str(cache)]
            if t.get("loop") == ["/usr/local/bin/k3s-uninstall.sh",
                                 "/usr/local/bin/k3s-agent-uninstall.sh"]:
                t["loop"] = [str(script)]
        node.mkdir()
        (node / "password").write_text("pw\n")
        cache.mkdir()
        script.write_text("")

        proc = _run(tmp_path, tasks)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert node.exists() and cache.exists()

        script.unlink()
        proc = _run(tmp_path, tasks)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert not node.exists() and not cache.exists()
