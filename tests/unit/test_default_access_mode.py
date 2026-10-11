"""storage setup records ReadWriteMany alongside the default storage class.

The class storage setup creates exists to share content volumes across
nodes, but create defaulted them to ReadWriteOnce, so an instance created
after it could not spread web pods across nodes. The default access mode
applies only together with the default storage class, and uninstall clears
both.
"""

import os

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
PLAYBOOKS = os.path.join(REPO_ROOT, "playbooks")
CREATE_K8S = os.path.join(REPO_ROOT, "roles", "create", "tasks",
                          "_kubernetes_setup.yml")
UNINSTALL = os.path.join(REPO_ROOT, "roles", "install", "tasks",
                         "uninstall_k3s.yml")


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _walk(tasks):
    for t in tasks or []:
        yield t
        for key in ("block", "rescue", "always"):
            yield from _walk(t.get(key))


def _named(path, name):
    return next(t for t in _walk(_load(path)) if t.get("name") == name)


def test_storage_setup_saves_read_write_many():
    for book in ("storage_setup_nfs.yml", "storage_setup_efs.yml"):
        path = os.path.join(PLAYBOOKS, book)
        names = [t.get("name") for t in _load(path)]
        task = _named(path, "Save default access mode")
        reg = task["canasta_registry"]
        assert reg["setting_key"] == "defaultAccessMode"
        assert reg["setting_value"] == "ReadWriteMany"
        assert task["delegate_to"] == "canasta_controller"
        assert names.index("Save default storage class") < names.index(
            "Save default access mode"), book


def _applies(variables):
    task = _named(CREATE_K8S, "Apply default access mode")
    templar = Templar(loader=DataLoader(), variables=variables)
    return all(templar.template(trust_as_template(
        "{{ %s }}" % cond)) is True for cond in task["when"])


def test_create_applies_it_with_the_default_class():
    assert _applies({"_storage_class_is_default": True,
                     "_default_access_mode": {"value": "ReadWriteMany"}})


def test_an_explicit_access_mode_wins():
    assert not _applies({"_storage_class_is_default": True,
                         "access_mode": "ReadWriteOnce",
                         "_default_access_mode": {"value": "ReadWriteMany"}})


def test_an_explicit_storage_class_does_not_get_it():
    assert not _applies({"storage_class": "local-path",
                         "_default_access_mode": {"value": "ReadWriteMany"}})


def test_no_saved_access_mode_changes_nothing():
    assert not _applies({"_storage_class_is_default": True,
                         "_default_access_mode": {"value": None}})


def test_the_default_class_marks_itself():
    task = _named(CREATE_K8S, "Apply default storage class")
    assert task["ansible.builtin.set_fact"]["_storage_class_is_default"] is True


def test_uninstall_clears_both_settings():
    block = _named(UNINSTALL, "Clear the default storage class that was on this cluster")
    keys = [t["canasta_registry"]["setting_key"] for t in block["block"]
            if "canasta_registry" in t]
    assert keys == ["defaultStorageClass", "defaultAccessMode"]
    for t in block["block"]:
        if "canasta_registry" in t:
            assert "setting_value" not in t["canasta_registry"]
