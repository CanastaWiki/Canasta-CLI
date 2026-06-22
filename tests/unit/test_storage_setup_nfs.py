"""Structural test for the storage-setup-nfs server reachability check."""

import os

import yaml


REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
NFS_PLAYBOOK = os.path.join(REPO_ROOT, "playbooks", "storage_setup_nfs.yml")


class TestNfsServerReachabilityCheck:
    """`storage setup nfs --server <addr>` points the StorageClass at a
    pre-existing NFS server. If that server isn't actually running, the
    StorageClass is dead and instance pods later hang on unmountable
    content PVCs. The playbook must probe the NFS port up front and fail
    with an actionable message — and only for --server, not the
    --install-server path (which sets the server up locally)."""

    def _tasks(self):
        with open(NFS_PLAYBOOK) as f:
            return yaml.safe_load(f)

    def test_probes_nfs_port_2049_gated_on_provided_server(self):
        probe = None
        for t in self._tasks():
            wf = t.get("ansible.builtin.wait_for") or t.get("wait_for")
            if isinstance(wf, dict) and wf.get("port") == 2049:
                probe = t
        assert probe is not None, (
            "storage_setup_nfs.yml must wait_for the NFS server on port "
            "2049 when --server is provided"
        )
        assert "install_server" in str(probe.get("when", "")), (
            "the NFS-port probe must be gated on (not install_server) so "
            "it runs only for --server"
        )

    def test_fails_with_actionable_message(self):
        with open(NFS_PLAYBOOK) as f:
            content = f.read()
        assert "not reachable on port 2049" in content, (
            "must fail with a clear message when the NFS server is "
            "unreachable"
        )
        assert "--install-server" in content, (
            "the failure message should point users to --install-server"
        )
