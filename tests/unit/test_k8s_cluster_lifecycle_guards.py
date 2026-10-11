"""Guards that keep one controller's clusters from stepping on each other.

- create applies the controller's default storage class only on a cluster
  that has it;
- a worker uninstall refuses when the control plane cannot say which
  volumes would be lost, unless --force;
- a node joins only one cluster in one role;
- install and uninstall leave a kubeconfig for other clusters intact.

Each test runs the relevant tasks in a localhost playbook, with absolute
host paths redirected into a temporary directory and `become` dropped.
"""

import os
import subprocess
import sys

import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CREATE_K8S = os.path.join(REPO_ROOT, "roles", "create", "tasks",
                          "_kubernetes_setup.yml")
UNINSTALL = os.path.join(REPO_ROOT, "roles", "install", "tasks",
                         "uninstall_k3s.yml")
CP_INSTALL = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks",
                          "k8s_install_k3s.yml")
AGENT_INSTALL = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks",
                             "k8s_install_k3s_agent.yml")

LOCAL_KUBECONFIG = "clusters:\n- cluster:\n    server: https://127.0.0.1:6443\n"
OTHER_KUBECONFIG = "clusters:\n- cluster:\n    server: https://10.9.9.9:6443\n"


def _strip_become(task):
    task.pop("become", None)
    for key in ("block", "rescue", "always"):
        for t in task.get(key, []):
            _strip_become(t)
    return task


def _walk(tasks):
    for t in tasks or []:
        yield t
        for key in ("block", "rescue", "always"):
            yield from _walk(t.get(key))


def _pick(path, names, root):
    text = open(path).read()
    for prefix in ("/usr/local/bin/", "/etc/systemd/system/"):
        text = text.replace(prefix, f"{root}{prefix}")
    by_name = {t.get("name"): t for t in _walk(yaml.safe_load(text))}
    return [_strip_become(by_name[n]) for n in names]


def _run(tmp_path, tasks, variables=None, path_dir=None):
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {"ansible_python_interpreter": sys.executable,
                 "ansible_env": {"HOME": str(tmp_path / "home")},
                 **(variables or {})},
        "tasks": tasks,
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0")
    if path_dir:
        env["PATH"] = f"{path_dir}{os.pathsep}{env['PATH']}"
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env,
                          cwd=tmp_path)
    proc.stdout += proc.stderr
    return proc


def _touch(root, path, content=""):
    target = root / path.lstrip("/")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    return target


class TestDefaultStorageClass:
    NAMES = ["Check that the default storage class exists on this cluster",
             "Note a default storage class missing from this cluster",
             "Apply default storage class"]

    def _create(self, tmp_path, present):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        kubectl = bin_dir / "kubectl"
        kubectl.write_text(
            "#!/bin/sh\n"
            f'[ "$3" = "{present}" ] && {{ echo storageclass/$3; exit 0; }}\n'
            'echo "Error from server (NotFound): storageclasses \\"$3\\" '
            'not found" >&2; exit 1\n')
        kubectl.chmod(0o755)
        tasks = _pick(CREATE_K8S, self.NAMES, tmp_path) + [
            {"ansible.builtin.debug": {
                "msg": "CLASS={{ storage_class | default('unset') }}"}}]
        return _run(tmp_path, tasks, {"_default_sc": {"value": "nfs-client"}},
                    path_dir=bin_dir)

    def test_a_class_on_this_cluster_is_applied(self, tmp_path):
        proc = self._create(tmp_path, "nfs-client")
        assert proc.returncode == 0, proc.stdout
        assert "CLASS=nfs-client" in proc.stdout

    def test_a_class_from_another_cluster_is_skipped(self, tmp_path):
        proc = self._create(tmp_path, "local-path")
        assert proc.returncode == 0, proc.stdout
        assert "CLASS=unset" in proc.stdout
        assert "is not on this" in proc.stdout


class TestWorkerVolumeGuard:
    NAMES = ["Refuse when the control plane cannot list the volumes",
             "Warn that the volume check was skipped",
             "Select the volumes this uninstall would destroy",
             "Refuse to destroy volumes bound to this node"]

    def _guard(self, tmp_path, pinned, force=False):
        return _run(tmp_path, _pick(UNINSTALL, self.NAMES, tmp_path), {
            "_pinned_pvs": pinned, "force": force, "cp_host": "cp",
            "_worker_hostname_raw": {"stdout": "worker1"}})

    def test_an_unreachable_control_plane_stops_the_uninstall(self, tmp_path):
        proc = self._guard(tmp_path, {"rc": 255, "stdout_lines": [],
                                      "stderr": "ssh: connect refused"})
        assert proc.returncode != 0
        assert "Could not list PersistentVolumes" in proc.stdout
        assert "ssh: connect refused" in proc.stdout

    def test_force_proceeds_with_a_warning(self, tmp_path):
        proc = self._guard(tmp_path, {"rc": 255, "stdout_lines": [],
                                      "stderr": "ssh: connect refused"},
                           force=True)
        assert proc.returncode == 0, proc.stdout
        assert "could not list PersistentVolumes" in proc.stdout

    def test_an_empty_listing_passes(self, tmp_path):
        proc = self._guard(tmp_path, {"rc": 0, "stdout_lines": [],
                                      "stderr": ""})
        assert proc.returncode == 0, proc.stdout


class TestControlPlaneInstallRole:
    NAMES = ["Check whether this host is a k3s worker",
             "Refuse to install a control plane on a worker"]

    def test_a_worker_is_refused(self, tmp_path):
        _touch(tmp_path, "/usr/local/bin/k3s-agent-uninstall.sh")
        proc = _run(tmp_path, _pick(CP_INSTALL, self.NAMES, tmp_path))
        assert proc.returncode != 0
        assert "already a k3s worker" in proc.stdout

    def test_a_fresh_host_passes(self, tmp_path):
        proc = _run(tmp_path, _pick(CP_INSTALL, self.NAMES, tmp_path))
        assert proc.returncode == 0, proc.stdout


class TestWorkerJoinRole:
    NAMES = ["Check for a k3s control plane on this host",
             "Check for a k3s worker on this host",
             "Refuse to join a control plane as a worker",
             "Read the control plane this worker joined",
             "Note the control plane this worker joined",
             "Refuse to join a worker of another cluster",
             "Skip if already joined to this cluster"]
    SERVER = "https://10.0.0.1:6443"

    def _join(self, tmp_path):
        return _run(tmp_path, _pick(AGENT_INSTALL, self.NAMES, tmp_path),
                    {"server": self.SERVER})

    def _worker_of(self, tmp_path, env_line):
        _touch(tmp_path, "/usr/local/bin/k3s-agent-uninstall.sh")
        _touch(tmp_path, "/etc/systemd/system/k3s-agent.service.env",
               "K3S_TOKEN_FILE=/etc/rancher/k3s/agent-join-token\n"
               + env_line + "\n")

    def test_a_fresh_host_goes_on_to_install(self, tmp_path):
        proc = self._join(tmp_path)
        assert proc.returncode == 0, proc.stdout
        assert "already installed" not in proc.stdout

    def test_a_control_plane_is_refused(self, tmp_path):
        _touch(tmp_path, "/usr/local/bin/k3s-uninstall.sh")
        proc = self._join(tmp_path)
        assert proc.returncode != 0
        assert "already a k3s control plane" in proc.stdout

    def test_a_worker_of_this_cluster_is_already_joined(self, tmp_path):
        self._worker_of(tmp_path, f"K3S_URL='{self.SERVER}'")
        proc = self._join(tmp_path)
        assert proc.returncode == 0, proc.stdout
        assert "already installed" in proc.stdout

    def test_a_worker_of_another_cluster_is_refused(self, tmp_path):
        self._worker_of(tmp_path, 'K3S_URL="https://10.0.0.9:6443"')
        proc = self._join(tmp_path)
        assert proc.returncode != 0
        assert "joined to https://10.0.0.9:6443" in proc.stdout


class TestInstallKubeconfigBackup:
    NAMES = ["Read an existing kubeconfig",
             "List the API servers in the existing kubeconfig",
             "Back up an existing kubeconfig for other clusters"]

    def _install(self, tmp_path, existing):
        kube = tmp_path / "home" / ".kube"
        kube.mkdir(parents=True)
        if existing is not None:
            (kube / "config").write_text(existing)
        proc = _run(tmp_path, _pick(CP_INSTALL, self.NAMES, tmp_path))
        assert proc.returncode == 0, proc.stdout
        return proc, sorted(kube.glob("config.*.bak"))

    def test_another_clusters_kubeconfig_is_backed_up(self, tmp_path):
        proc, backups = self._install(tmp_path, OTHER_KUBECONFIG)
        assert len(backups) == 1
        assert backups[0].read_text() == OTHER_KUBECONFIG
        assert str(backups[0]) in proc.stdout

    def test_the_local_k3s_kubeconfig_is_not(self, tmp_path):
        _, backups = self._install(tmp_path, LOCAL_KUBECONFIG)
        assert backups == []

    def test_no_kubeconfig_needs_no_backup(self, tmp_path):
        _, backups = self._install(tmp_path, None)
        assert backups == []


class TestUninstallKubeconfig:
    NAMES = ["Read kubeconfig", "List the API servers in the kubeconfig",
             "Remove kubeconfig", "Report kubeconfig kept"]

    def _uninstall(self, tmp_path, existing):
        config = tmp_path / "home" / ".kube" / "config"
        config.parent.mkdir(parents=True)
        config.write_text(existing)
        with open(UNINSTALL) as f:
            block = next(t for t in yaml.safe_load(f)
                         if t.get("name") == "Uninstall k3s")
        proc = _run(tmp_path, [{
            "name": "kubeconfig cleanup",
            "vars": block["vars"],
            "block": _pick(UNINSTALL, self.NAMES, tmp_path)}])
        assert proc.returncode == 0, proc.stdout
        return proc, config

    def test_the_local_k3s_kubeconfig_is_removed(self, tmp_path):
        _, config = self._uninstall(tmp_path, LOCAL_KUBECONFIG)
        assert not config.exists()

    def test_another_clusters_kubeconfig_is_kept(self, tmp_path):
        proc, config = self._uninstall(tmp_path, OTHER_KUBECONFIG)
        assert config.read_text() == OTHER_KUBECONFIG
        assert "https://10.9.9.9:6443" in proc.stdout

    def test_a_merged_kubeconfig_is_kept(self, tmp_path):
        merged = LOCAL_KUBECONFIG + OTHER_KUBECONFIG.split("\n", 1)[1]
        _, config = self._uninstall(tmp_path, merged)
        assert config.exists()
