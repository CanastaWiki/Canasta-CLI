"""Guards for `canasta storage setup nfs --install-server` exports.

The share is exported only to the cluster's node addresses and pod networks,
through a sync that follows nodes joining and leaving, and `canasta upgrade`
moves an export written by an earlier release onto that sync.
"""

import os
import re

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
PLAYBOOK = os.path.join(REPO_ROOT, "playbooks", "storage_setup_nfs.yml")
TASKS = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks")
SYNC_TASKS = os.path.join(TASKS, "nfs_exports_sync.yml")
UPGRADE_TASKS = os.path.join(TASKS, "k8s_nfs_exports_upgrade.yml")
UPGRADE_MAIN = os.path.join(REPO_ROOT, "roles", "upgrade", "tasks", "main.yml")
SCRIPT = os.path.join(REPO_ROOT, "roles", "orchestrator", "files", "nfs",
                      "canasta-nfs-exports-sync")


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f) or []


def _walk(tasks):
    for t in tasks or []:
        if not isinstance(t, dict):
            continue
        yield t
        for nested in ("block", "rescue", "always"):
            yield from _walk(t.get(nested))


def _named(path, name):
    return next(t for t in _walk(_load(path)) if t.get("name") == name)


class TestSetup:
    def test_no_any_host_export_is_written(self):
        text = open(PLAYBOOK).read()
        assert "*(rw" not in text.replace("\\*\\(", "")
        for t in _walk(_load(PLAYBOOK)):
            lif = t.get("ansible.builtin.lineinfile") or {}
            if lif.get("path") == "/etc/exports":
                assert lif.get("state") == "absent"

    def test_share_goes_through_the_sync(self):
        task = _named(PLAYBOOK, "Export the share to the cluster's nodes only")
        assert task["ansible.builtin.include_role"]["tasks_from"] == (
            "nfs_exports_sync.yml")
        assert task["vars"]["_nfs_sync_shares"] == ["{{ share }}"]

    def test_include_is_not_inside_a_conditional_block(self):
        for t in _load(PLAYBOOK):
            for inner in t.get("block") or []:
                assert "ansible.builtin.include_role" not in inner

    def test_server_package_follows_the_os_family(self):
        task = _named(PLAYBOOK, "Install NFS server packages")
        name = task["ansible.builtin.package"]["name"]
        assert "nfs-utils" in name and "nfs-kernel-server" in name
        assert "ansible_os_family" in name

    def test_service_name_exists_on_both_families(self):
        task = _named(PLAYBOOK, "Ensure NFS server is running")
        assert task["ansible.builtin.service"]["name"] == "nfs-server"


class TestSync:
    def test_script_exports_only_to_listed_clients(self):
        text = open(SCRIPT).read()
        assert "*(" not in text
        assert re.search(r'InternalIP.*ExternalIP.*PodCIDR', text)

    def test_script_keeps_exports_when_api_is_unreachable(self):
        text = open(SCRIPT).read()
        guard = text.index('[ -n "$clients" ] || exit 1')
        assert guard < text.index('install -m 0644 "$tmp" "$OUT"')

    def test_timer_is_enabled(self):
        task = _named(SYNC_TASKS, "Enable the NFS exports sync timer")
        assert task["ansible.builtin.systemd"]["name"] == (
            "canasta-nfs-exports-sync.timer")
        assert task["ansible.builtin.systemd"]["enabled"] is True

    def test_missing_export_fails_unless_optional(self):
        task = _named(SYNC_TASKS, "Fail if a share is not exported")
        assert "not (_nfs_sync_ok | bool)" in task["when"]
        assert "_nfs_sync_required | default(true) | bool" in task["when"]


class TestUpgrade:
    def test_runs_for_kubernetes_instances(self):
        task = _named(UPGRADE_MAIN,
                      "Limit Canasta NFS exports to the cluster's nodes (Kubernetes)")
        assert "k8s_nfs_exports_upgrade.yml" in task["ansible.builtin.include_tasks"]
        assert "kubernetes" in task["when"]

    def test_only_the_exact_earlier_line_is_matched(self):
        task = _named(UPGRADE_TASKS,
                      "Find shares exported to any host by an earlier storage setup")
        expr = task["ansible.builtin.set_fact"]["_nfs_upg_legacy_shares"]
        pattern = re.search(r"select\('match', '([^']+)'\)", expr).group(1)
        assert re.match(pattern,
                        "/srv/nfs/canasta *(rw,sync,no_subtree_check,no_root_squash)")
        # Operator-written exports are left alone.
        assert not re.match(pattern, "/srv/nfs/canasta 10.0.0.0/8(rw,sync)")
        assert not re.match(pattern, "/data *(ro,sync)")

    def test_sync_failure_does_not_end_the_upgrade(self):
        task = _named(UPGRADE_TASKS, "Export those shares to the cluster's nodes only")
        assert task["vars"]["_nfs_sync_required"] is False

    def test_old_export_is_kept_unless_the_sync_worked(self):
        task = _named(UPGRADE_TASKS, "Remove the any-host exports from /etc/exports")
        assert task["when"] == "_nfs_sync_ok | default(false) | bool"
