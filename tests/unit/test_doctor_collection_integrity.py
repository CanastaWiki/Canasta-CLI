"""doctor notices when a collection's modules have been replaced by stubs.

ansible-lint materializes a stub for every module a project names in
`mock_modules`. Written into the operator's own collection tree, the stub
replaces the real module inside a collection that still reports its
pinned version — so `ansible-galaxy collection list` shows it healthy,
and neither `install` nor `install --upgrade` repairs it, because both
see the version already present and skip.

The stub's argspec is empty, so the module rejects every option it is
given. It surfaces only against a remote host: a controller-side call is
handled by the collection's action plugin, which is not mocked.

Nothing else in the CLI can see this, which is why doctor has to.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from direct_commands import doctor  # noqa: E402

MOCK_BODY = """DOCUMENTATION = '''
description: Mocked
author:
    - ansible-lint (@nobody)
'''
from ansible.module_utils.basic import AnsibleModule


def main():
    AnsibleModule(argument_spec=dict()).exit_json(changed=False)
"""

REAL_BODY = """DOCUMENTATION = '''
description: Manage Kubernetes objects
author:
    - Chris Houseknecht (@chouseknecht)
'''
"""


@pytest.fixture
def collections(tmp_path, monkeypatch):
    """An ANSIBLE_COLLECTIONS_PATH with kubernetes.core installed."""
    mod_dir = (tmp_path / "ansible_collections" / "kubernetes" / "core"
               / "plugins" / "modules")
    mod_dir.mkdir(parents=True)
    monkeypatch.setenv("ANSIBLE_COLLECTIONS_PATH", str(tmp_path))
    monkeypatch.delenv("ANSIBLE_COLLECTIONS_PATHS", raising=False)
    return mod_dir


class TestDetection:
    def test_a_healthy_collection_reports_nothing(self, collections):
        (collections / "k8s.py").write_text(REAL_BODY)
        assert doctor._mocked_collection_modules() == []
        assert doctor._collection_integrity_lines() == []

    def test_a_stub_is_found(self, collections):
        (collections / "k8s.py").write_text(MOCK_BODY)
        assert doctor._mocked_collection_modules() == ["kubernetes.core: k8s"]

    def test_only_the_stubs_are_named(self, collections):
        (collections / "k8s.py").write_text(MOCK_BODY)
        (collections / "k8s_info.py").write_text(MOCK_BODY)
        (collections / "k8s_cp.py").write_text(REAL_BODY)
        assert doctor._mocked_collection_modules() == [
            "kubernetes.core: k8s", "kubernetes.core: k8s_info"]

    def test_a_missing_collection_is_not_an_error(self, tmp_path, monkeypatch):
        """Not having a collection installed is a different problem."""
        monkeypatch.setenv("ANSIBLE_COLLECTIONS_PATH", str(tmp_path))
        assert doctor._mocked_collection_modules() == []

    def test_only_pinned_collections_are_inspected(self, tmp_path, monkeypatch):
        """A stub in some unrelated collection is not the CLI's problem."""
        mod_dir = (tmp_path / "ansible_collections" / "community" / "general"
                   / "plugins" / "modules")
        mod_dir.mkdir(parents=True)
        (mod_dir / "whatever.py").write_text(MOCK_BODY)
        monkeypatch.setenv("ANSIBLE_COLLECTIONS_PATH", str(tmp_path))
        assert doctor._mocked_collection_modules() == []


class TestReport:
    def test_the_report_names_the_repair(self, collections):
        (collections / "k8s.py").write_text(MOCK_BODY)
        text = "\n".join(doctor._collection_integrity_lines())
        assert "--force" in text, (
            "only --force rewrites the files; without it the operator "
            "re-runs a command that skips and changes nothing"
        )
        assert "kubernetes.core: k8s" in text
        assert "Supported parameters include" in text, (
            "the report has to quote the runtime error, since that string "
            "is all an operator has to search for"
        )

    def test_the_report_says_upgrade_will_not_help(self, collections):
        (collections / "k8s.py").write_text(MOCK_BODY)
        text = "\n".join(doctor._collection_integrity_lines())
        assert "--upgrade" in text


class TestPinnedCollections:
    def test_requirements_pins_are_read(self):
        """Drives which collections are inspected; empty means no checking."""
        pinned = doctor._pinned_collections()
        assert ("kubernetes", "core") in pinned, (
            "kubernetes.core is the collection whose stubs break remote K8s"
        )
        assert all(len(p) == 2 for p in pinned)


class TestSearchPaths:
    def test_env_var_wins(self, monkeypatch):
        monkeypatch.setenv("ANSIBLE_COLLECTIONS_PATH", "/a:/b")
        assert doctor._collection_search_paths()[:2] == ["/a", "/b"]

    def test_default_follows_ansible_home(self, monkeypatch):
        monkeypatch.delenv("ANSIBLE_COLLECTIONS_PATH", raising=False)
        monkeypatch.delenv("ANSIBLE_COLLECTIONS_PATHS", raising=False)
        monkeypatch.setenv("ANSIBLE_HOME", "/custom/ansible")
        assert "/custom/ansible/collections" in doctor._collection_search_paths()
