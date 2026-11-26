"""Structural coverage for the Kubernetes side of `canasta devmode` and for
`canasta storage setup nfs`: devmode is declared Compose-only so the CLI
refuses it on Kubernetes, and NFS setup installs the NFS CSI driver with
Helm, creates an `nfs` StorageClass pointing at the NFS server, and refuses
to run without a server."""

import os

import yaml

from test_devmode_storage import REPO_ROOT, _load


STORAGE_NFS = os.path.join(REPO_ROOT, "playbooks", "storage_setup_nfs.yml")


class TestDevmodeGuards:
    def test_declared_compose_only(self):
        # canasta.py refuses devmode on Kubernetes from this key.
        with open(os.path.join(REPO_ROOT, "meta", "command_definitions.yml")) as f:
            commands = {c["name"]: c for c in yaml.safe_load(f)["commands"]}
        for name in ['devmode_enable', 'devmode_disable']:
            assert commands[name].get("orchestrators") == ['compose'], name


class TestNfsStorageSetup:
    """`canasta storage setup nfs` installs the NFS CSI driver and creates an
    `nfs` StorageClass pointing at the NFS server."""

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
