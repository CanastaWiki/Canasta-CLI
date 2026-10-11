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
        assert task["when"] == "instance_is_k8s | bool"

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
        # A re-run after an interrupted uninstall finds k3s already gone and
        # must still withdraw the exports.
        assert "when" not in task

    def test_rerun_removes_only_a_kubeconfig_for_the_removed_local_k3s(self):
        # Interrupted after the k3s script but before its own cleanup, the
        # run leaves the kubeconfig, which the withdrawal reads as a live
        # cluster. A re-run removes it when it points at the local k3s.
        import base64
        from ansible.parsing.dataloader import DataLoader
        from ansible.template import Templar, trust_as_template
        task = _named(UNINSTALL_K3S, "Remove the kubeconfig of the removed local k3s")
        assert task["ansible.builtin.file"]["state"] == "absent"

        def removes(server, script=""):
            cfg = "clusters:\n- cluster:\n    server: %s\n" % server
            templar = Templar(loader=DataLoader(), variables={
                "_k3s_uninstall_script": script,
                "_k3s_leftover_kubeconfig": {
                    "content": base64.b64encode(cfg.encode()).decode()}})
            return all(templar.template(trust_as_template("{{ %s }}" % c))
                       is True for c in task["when"])

        assert removes("https://127.0.0.1:6443")
        assert removes("https://localhost:6443")
        assert not removes("https://k8s.example.com:6443")
        assert not removes("https://10.0.0.5:6443")
        assert not removes("https://127.0.0.1:6443",
                           script="/usr/local/bin/k3s-uninstall.sh")

    def test_the_stale_kubeconfig_goes_before_the_withdrawal(self):
        names = [t.get("name") for t in _load(UNINSTALL_K3S)]
        assert names.index("Remove the kubeconfig of the removed local k3s") < (
            names.index("Withdraw NFS exports that followed this cluster"))

    def test_withdrawal_waits_for_the_cluster_to_be_gone(self):
        # Ungated in the uninstall, so the removal itself must leave the
        # exports alone while the cluster's kubeconfig still exists.
        block = _named(REMOVE_TASKS, "Remove the NFS exports sync when its cluster is gone")
        acting = [t for t in block["block"]
                  if t.get("name") in ("Stop the NFS exports sync timer",
                                       "Remove the NFS exports sync files")]
        assert len(acting) == 2
        for t in acting:
            assert t["when"] == "not _nfs_rm_kubeconfig_stat.stat.exists"

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


class TestSyncScriptClients:
    """Run the real sync script against a stub kubectl."""

    NODES = (
        "Hostname cp\n"
        "InternalIP 10.0.0.5\n"
        "ExternalIP 203.0.113.7\n"
        "InternalIP fd00::5\n"
        "PodCIDR 10.42.0.0/24\n"
        "PodCIDR fd00:42::/64\n"
    )

    def _run(self, tmp_path, nodes, shell="/bin/sh"):
        import shutil
        import subprocess
        etc = tmp_path / "etc"
        etc.mkdir()
        (etc / "nfs-shares").write_text("/srv/nfs/canasta\n")
        bindir = tmp_path / "bin"
        bindir.mkdir()
        (tmp_path / "nodes").write_text(nodes)
        kubectl = bindir / "kubectl"
        kubectl.write_text('#!/bin/sh\ncat "%s"\n' % (tmp_path / "nodes"))
        exportfs = bindir / "exportfs"
        exportfs.write_text("#!/bin/sh\nexit 0\n")
        for f in (kubectl, exportfs):
            f.chmod(0o755)
        text = open(SCRIPT).read()
        for const, path in (("/etc/canasta/nfs-exports.env", etc / "env"),
                            ("/etc/canasta/nfs-shares", etc / "nfs-shares"),
                            ("/etc/exports.d/canasta.exports", etc / "out"),
                            ("mkdir -p /etc/exports.d", "true")):
            assert const in text
            text = text.replace(const, str(path))
        script = tmp_path / "sync"
        script.write_text(text)
        # Files a glob in the client list would expand to.
        work = tmp_path / "work"
        work.mkdir()
        (work / "evil(rw)").write_text("")
        (work / "other").write_text("")
        env = dict(os.environ, PATH="%s:%s" % (bindir, os.environ["PATH"]),
                   KUBECONFIG=str(tmp_path / "kubeconfig"))
        if not shutil.which(shell):
            import pytest
            pytest.skip(shell + " not available")
        proc = subprocess.run([shell, str(script)], cwd=work, env=env,
                              capture_output=True, text=True)
        out = etc / "out"
        clients = out.read_text().split()[1:] if out.exists() else None
        return proc, clients

    def test_node_addresses_and_pod_cidrs_are_exported(self, tmp_path):
        proc, clients = self._run(tmp_path, self.NODES)
        assert proc.returncode == 0, proc.stderr
        opts = "(rw,sync,no_subtree_check,no_root_squash)"
        assert sorted(clients) == sorted(c + opts for c in (
            "10.0.0.5", "203.0.113.7", "fd00::5", "10.42.0.0/24",
            "fd00:42::/64"))
        assert proc.stderr == ""

    def test_invalid_addresses_are_dropped_and_logged(self, tmp_path):
        bad = ["*", "0.0.0.0/0", "10.0.0.0/7", "::/0", "fd00::/8",
               "*.example.com", "host.example.com", "10.0.0.6(rw)",
               "10.0.0.256", "10.0.0", "10.0.0.7/33", "10.0.0.8/",
               "10.0.0.0/8/8", "@netgroup"]
        nodes = self.NODES + "".join("InternalIP %s\n" % b for b in bad)
        nodes += "ExternalIP 10.0.0.9 *\nPodCIDR\n"
        proc, clients = self._run(tmp_path, nodes)
        assert proc.returncode == 0, proc.stderr
        hosts = {c.split("(")[0] for c in clients}
        assert hosts == {"10.0.0.5", "203.0.113.7", "fd00::5",
                         "10.42.0.0/24", "fd00:42::/64"}
        for b in bad:
            assert '"%s"' % b in proc.stderr
        assert '"10.0.0.9 *"' in proc.stderr

    def test_a_star_is_not_glob_expanded(self, tmp_path):
        proc, clients = self._run(tmp_path, "InternalIP 10.0.0.5\nInternalIP *\n")
        assert proc.returncode == 0, proc.stderr
        assert [c.split("(")[0] for c in clients] == ["10.0.0.5"]

    def test_only_invalid_addresses_leave_the_exports_alone(self, tmp_path):
        proc, clients = self._run(tmp_path, "InternalIP *\nPodCIDR 0.0.0.0/0\n")
        assert proc.returncode == 1
        assert clients is None

    def test_runs_under_dash(self, tmp_path):
        proc, clients = self._run(tmp_path, self.NODES + "InternalIP *\n",
                                  shell="dash")
        assert proc.returncode == 0, proc.stderr
        assert len(clients) == 5

    def test_globbing_is_off_before_the_client_loop(self):
        text = open(SCRIPT).read()
        assert re.search(r"^set -f$", text, re.M)
        assert text.index("set -f") < text.index("for client in $clients")


class TestSyncKubeconfig:
    """The root-run sync must only read a root-owned kubeconfig."""

    def _render(self, k3s_exists, kubeconfig):
        from ansible.parsing.dataloader import DataLoader
        from ansible.template import Templar, trust_as_template
        task = _named(SYNC_TASKS, "Record the kubeconfig the sync uses")
        variables = {"_nfs_sync_k3s_kubeconfig": {"stat": {"exists": k3s_exists}},
                     "_nfs_sync_kubeconfig": kubeconfig}
        templar = Templar(loader=DataLoader(), variables=variables)
        runs = templar.template(trust_as_template(
            "{{ %s }}" % task["when"])) is True
        content = templar.template(trust_as_template(
            task["ansible.builtin.copy"]["content"]))
        return runs, content

    def test_k3s_server_uses_the_k3s_kubeconfig(self):
        runs, content = self._render(True, "/home/u/.kube/config")
        assert runs
        assert content == "KUBECONFIG=/etc/rancher/k3s/k3s.yaml\n"

    def test_other_hosts_use_a_root_owned_copy(self):
        runs, content = self._render(False, "/home/u/.kube/config")
        assert runs
        assert content == ("KUBECONFIG=/etc/canasta/nfs-kubeconfig\n"
                           "CANASTA_KUBECONFIG_SOURCE=/home/u/.kube/config\n")
        copy = _named(SYNC_TASKS, "Copy the kubeconfig for the sync")
        mod = copy["ansible.builtin.copy"]
        assert mod["dest"] == "/etc/canasta/nfs-kubeconfig"
        assert mod["remote_src"] is True
        assert (mod["owner"], mod["group"], mod["mode"]) == ("root", "root", "0600")
        assert copy["become"] is True
        assert "not _nfs_sync_k3s_kubeconfig.stat.exists" in copy["when"]

    def test_nothing_to_record_without_either(self):
        runs, _ = self._render(False, "")
        assert not runs

    def test_upgrade_refreshes_the_kubeconfig_of_an_installed_sync(self):
        task = _named(UPGRADE_TASKS, "Find the kubeconfig for the export sync")
        assert "_nfs_upg_sync.stat.exists" in task["when"]

    def test_removal_follows_the_copied_kubeconfig_source(self, tmp_path):
        import subprocess
        task = _named(REMOVE_TASKS, "Read the kubeconfig the sync uses")
        cmd = task["ansible.builtin.shell"]["cmd"]
        env_file = tmp_path / "env"
        cmd = cmd.replace("/etc/canasta/nfs-exports.env", str(env_file))

        def path():
            return subprocess.run(["/bin/sh", "-c", cmd], capture_output=True,
                                  text=True, env={"PATH": os.environ["PATH"]}
                                  ).stdout.strip()

        env_file.write_text("KUBECONFIG=/etc/canasta/nfs-kubeconfig\n"
                            "CANASTA_KUBECONFIG_SOURCE=/home/u/.kube/config\n")
        assert path() == "/home/u/.kube/config"
        env_file.write_text("KUBECONFIG=/etc/rancher/k3s/k3s.yaml\n")
        assert path() == "/etc/rancher/k3s/k3s.yaml"
