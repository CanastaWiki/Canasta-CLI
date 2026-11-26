"""Commands whose fast path is complete own no Ansible twin."""

import os

import yaml

REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", ".."))


def _defs():
    with open(os.path.join(REPO_ROOT, "meta", "command_definitions.yml")) as f:
        return {c["name"]: c for c in yaml.safe_load(f)["commands"]}


def test_extension_and_skin_list_are_direct_only():
    defs = _defs()
    for name in ("extension_list", "skin_list"):
        assert defs[name].get("direct_only") is True
        assert "playbook" not in defs[name]
    assert not os.path.exists(os.path.join(
        REPO_ROOT, "roles", "extensions_skins", "tasks", "list.yml"))
