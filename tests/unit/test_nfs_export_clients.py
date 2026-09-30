"""Guards for `canasta storage setup nfs --install-server` exports.

The share is exported only to the cluster's node addresses and pod networks,
through a sync that follows nodes joining and leaving, `canasta upgrade`
moves an export written by an earlier release onto that sync, and
`canasta uninstall k8s` withdraws the export once the cluster is gone.
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
REMOVE_TASKS = os.path.join(TASKS, "nfs_exports_remove.yml")
UNINSTALL_K3S = os.path.join(REPO_ROOT, "roles", "install", "tasks",
                             "uninstall_k3s.yml")
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
        guard = text.index('if [ -z "$clients" ]; then')
        assert guard < text.index("exit 1") < text.index(
            'install -m 0644 "$tmp" "$OUT"')

    def test_script_reports_why_it_kept_the_exports(self):
        text = open(SCRIPT).read()
        failure = text[text.index('if [ -z "$clients" ]; then'):]
        failure = failure[:failure.index("exit 1")]
        assert "could not list the cluster's nodes" in failure
        assert 'cat "$err" >&2' in failure

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


class TestUninstall:
    def _include(self):
        return _named(UNINSTALL_K3S,
                      "Withdraw NFS exports that followed this cluster")

    def test_uninstall_withdraws_the_exports(self):
        task = self._include()
        assert task["ansible.builtin.include_tasks"].endswith(
            "/roles/orchestrator/tasks/nfs_exports_remove.yml")
        assert task["when"] == "_k3s_uninstall_script | length > 0"

    def test_include_runs_after_k3s_is_removed_and_outside_a_block(self):
        top = _load(UNINSTALL_K3S)
        names = [t.get("name") for t in top]
        assert self._include() in top
        assert names.index("Uninstall k3s") < names.index(
            "Withdraw NFS exports that followed this cluster")

    def test_every_file_the_sync_installs_is_removed(self):
        installed = {"/etc/exports.d/canasta.exports"}
        for t in _walk(_load(SYNC_TASKS)):
            for key in ("ansible.builtin.copy", "ansible.builtin.lineinfile"):
                mod = t.get(key) or {}
                dest = mod.get("dest") or mod.get("path")
                if not dest:
                    continue
                if "{{ item }}" in dest:
                    installed.update(dest.replace("{{ item }}", i)
                                     for i in t["loop"])
                else:
                    installed.add(dest)
        task = _named(REMOVE_TASKS, "Remove the NFS exports sync files")
        assert task["ansible.builtin.file"]["state"] == "absent"
        assert installed <= set(task["loop"])

    def test_removal_waits_for_the_cluster_to_be_gone(self):
        gate = "not _nfs_rm_kubeconfig_stat.stat.exists"
        for name in ("Stop the NFS exports sync timer",
                     "Remove the NFS exports sync files",
                     "Withdraw the NFS exports"):
            assert _named(REMOVE_TASKS, name)["when"] == gate

    def test_kubeconfig_default_matches_the_sync(self):
        task = _named(REMOVE_TASKS, "Read the kubeconfig the sync uses")
        cmd = task["ansible.builtin.shell"]["cmd"]
        assert "/etc/canasta/nfs-exports.env" in cmd
        assert "/etc/rancher/k3s/k3s.yaml" in open(SCRIPT).read()
        assert "${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}" in cmd

    def test_share_contents_are_left_alone(self):
        task = _named(REMOVE_TASKS, "Remove the NFS exports sync files")
        assert not any(p.startswith("/srv") for p in task["loop"])
        text = open(REMOVE_TASKS).read()
        assert "rm -rf" not in text

    def test_uninstall_removes_the_kubeconfig_the_install_wrote(self):
        top = _load(UNINSTALL_K3S)
        names = [t.get("name") for t in top]
        gather = _named(UNINSTALL_K3S, "Gather minimal facts for HOME directory")
        assert gather["ansible.builtin.setup"]["filter"] == "ansible_env"
        assert "become" not in gather
        assert names.index("Gather minimal facts for HOME directory") < (
            names.index("Uninstall k3s"))
        remove = _named(UNINSTALL_K3S, "Remove kubeconfig")
        assert remove["ansible.builtin.file"]["path"] == (
            "{{ ansible_env.HOME }}/.kube/config")
