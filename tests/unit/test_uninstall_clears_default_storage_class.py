"""Uninstalling a control plane clears a default storage class it held.

`storage setup nfs|efs` records its class as Settings.defaultStorageClass,
which `create` inherits. After the cluster holding that class is
uninstalled the default names a class that no longer exists, and the next
Kubernetes create waits on PVCs that can never bind. The default is
cleared only when the class was on the cluster being removed, so a
default belonging to another cluster managed from the same controller is
left alone.
"""

import os

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
UNINSTALL = os.path.join(
    REPO_ROOT, "roles", "install", "tasks", "uninstall_k3s.yml")


def _tasks():
    with open(UNINSTALL) as f:
        return yaml.safe_load(f) or []


def _index(name):
    return next(i for i, t in enumerate(_tasks()) if t.get("name") == name)


def _task(name):
    return _tasks()[_index(name)]


READ = "Read the default storage class"
CHECK = "Check whether the default storage class is on this cluster"
CLEAR = "Clear the default storage class that was on this cluster"


class TestReadTheDefault:
    def test_it_reads_the_setting_from_the_controller(self):
        task = _task(READ)
        assert task["canasta_registry"] == {
            "state": "get_setting", "setting_key": "defaultStorageClass"}
        assert task["delegate_to"] == "canasta_controller"
        assert task["vars"]["ansible_connection"] == "local"

    def test_only_a_control_plane_uninstall_reads_it(self):
        assert _task(READ)["when"] == "_k3s_role_label == 'control plane'"


class TestCheckTheCluster:
    def test_it_looks_the_class_up_before_the_cluster_is_removed(self):
        assert _index(READ) < _index(CHECK) < _index("Uninstall k3s")

    def test_it_asks_the_cluster_for_that_class(self):
        argv = _task(CHECK)["ansible.builtin.command"]["argv"]
        assert argv[:4] == ["/usr/local/bin/k3s", "kubectl", "get",
                            "storageclass"]
        assert argv[4] == "{{ _uninstall_default_sc.value }}"

    def test_it_runs_only_when_a_default_is_set(self):
        when = _task(CHECK)["when"]
        assert "_k3s_role_label == 'control plane'" in when
        assert any("_uninstall_default_sc.value" in w for w in when)

    def test_a_failed_lookup_does_not_stop_the_uninstall(self):
        assert _task(CHECK)["failed_when"] is False


class TestClearTheDefault:
    def test_it_clears_after_the_cluster_is_removed(self):
        assert _index("Uninstall k3s") < _index(CLEAR)

    def test_it_clears_only_a_class_found_on_this_cluster(self):
        assert _task(CLEAR)["when"] == (
            "(_uninstall_default_sc_here.rc | default(1)) == 0")

    def test_it_unsets_the_setting_on_the_controller(self):
        clear = _task(CLEAR)["block"][0]
        assert clear["canasta_registry"] == {
            "state": "set_setting", "setting_key": "defaultStorageClass"}
        assert clear["delegate_to"] == "canasta_controller"
        assert clear["vars"]["ansible_connection"] == "local"

    def test_it_reports_the_cleared_class(self):
        report = _task(CLEAR)["block"][1]
        assert "_uninstall_default_sc.value" in (
            report["ansible.builtin.debug"]["msg"])
