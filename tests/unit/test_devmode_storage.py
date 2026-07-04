"""Structural coverage for two commands that had none (#758):
`canasta devmode enable/disable` and `canasta storage setup nfs`.

These assert the task structure (guards, generated resources) rather than
runtime behavior. Full runtime coverage needs infra not available in CI
(a local Docker build for devmode's xdebug image; an NFS server for
storage) and is exercised by the hands-on /e2e run instead.
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
DEVMODE_TASKS = os.path.join(REPO_ROOT, "roles", "devmode", "tasks")
STORAGE_NFS = os.path.join(REPO_ROOT, "playbooks", "storage_setup_nfs.yml")


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f)


class TestDevmodeGuards:
    """devmode is Compose-and-localhost only: it extracts code and builds an
    xdebug image on the local machine. Both enable and disable must refuse a
    remote instance and a Kubernetes instance, and check the dev-mode state,
    before doing any work."""

    def _fail_conditions(self, tasks):
        # Map each ansible.builtin.fail task's name -> its `when` (as a string).
        out = {}
        for t in tasks:
            if not isinstance(t, dict):
                continue
            if "ansible.builtin.fail" in t:
                out[t.get("name", "")] = str(t.get("when", ""))
        return out

    def test_enable_guards(self):
        fails = self._fail_conditions(_load(os.path.join(DEVMODE_TASKS, "enable.yml")))
        # localhost-only
        local = next(c for n, c in fails.items() if "requires local" in n.lower())
        assert "localhost" in local and "!=" in local
        # not on kubernetes
        k8s = next(c for n, c in fails.items() if "not applicable" in n.lower()
                   or "kubernetes" in n.lower())
        assert "kubernetes" in k8s or "k8s" in k8s
        # not already in dev mode
        assert any("already in dev" in n.lower() for n in fails)

    def test_disable_guards(self):
        fails = self._fail_conditions(_load(os.path.join(DEVMODE_TASKS, "disable.yml")))
        local = next(c for n, c in fails.items() if "requires local" in n.lower())
        assert "localhost" in local and "!=" in local
        assert any("not applicable" in n.lower() or "kubernetes" in n.lower()
                   for n in fails)
        # must be IN dev mode to disable
        assert any(n for n in fails if "in dev" in n.lower()
                   and "already" not in n.lower())


class TestDevmodeRegistryUpdate:
    """canasta_registry state=present rebuilds the whole record from params, so
    the registry-update in enable/disable must forward every field it wants
    kept — including dockerHost (set by create --docker-host or rootless
    auto-detect), or toggling devmode silently drops it."""

    def _registry_update(self, tasks):
        for t in tasks:
            if isinstance(t, dict) and "canasta_registry" in t:
                params = t["canasta_registry"]
                if params.get("state") == "present":
                    return params
        raise AssertionError("no canasta_registry state=present task found")

    def test_enable_forwards_docker_host(self):
        params = self._registry_update(
            _load(os.path.join(DEVMODE_TASKS, "enable.yml")))
        assert "docker_host" in params
        assert "dockerHost" in params["docker_host"]
        assert "default(omit)" in params["docker_host"]

    def test_disable_forwards_docker_host(self):
        params = self._registry_update(
            _load(os.path.join(DEVMODE_TASKS, "disable.yml")))
        assert "docker_host" in params
        assert "dockerHost" in params["docker_host"]
        assert "default(omit)" in params["docker_host"]


class TestNfsStorageSetup:
    """`canasta storage setup nfs` installs the NFS CSI driver and creates an
    `nfs` StorageClass pointing at the NFS server (#758)."""

    def _tasks(self):
        # playbooks/*.yml are flat task lists included by canasta.yml.
        loaded = _load(STORAGE_NFS)
        return loaded[0]["tasks"] if isinstance(loaded[0], dict) and "tasks" in loaded[0] \
            else loaded

    def test_installs_nfs_csi_driver_via_helm(self):
        tasks = self._tasks()
        helm = next(t for t in tasks if isinstance(t, dict)
                    and t.get("kubernetes.core.helm", {}).get("name") == "csi-driver-nfs")
        assert helm["kubernetes.core.helm"]["chart_ref"].endswith("csi-driver-nfs")
        assert helm["kubernetes.core.helm"]["wait"] is True

    def test_creates_nfs_storageclass(self):
        tasks = self._tasks()
        sc = next(t for t in tasks if isinstance(t, dict)
                  and isinstance(t.get("kubernetes.core.k8s"), dict)
                  and t["kubernetes.core.k8s"].get("definition", {}).get("kind")
                  == "StorageClass")
        defn = sc["kubernetes.core.k8s"]["definition"]
        assert defn["provisioner"] == "nfs.csi.k8s.io"
        assert "nfs" in str(defn["metadata"]["name"])  # default 'nfs'
        # Delete so a deleted instance's pvc-* export dirs are reclaimed
        # instead of leaking; see test_storage_setup_nfs.py.
        assert defn["reclaimPolicy"] == "Delete"
        params = defn["parameters"]
        assert "server" in params and "share" in params

    def test_requires_a_server(self):
        # Either --server or --install-server; fail loudly if neither.
        tasks = self._tasks()
        guard = next(t for t in tasks if isinstance(t, dict)
                     and "ansible.builtin.fail" in t
                     and "server" in str(t.get("when", "")).lower())
        assert "_nfs_server" in str(guard["when"])
