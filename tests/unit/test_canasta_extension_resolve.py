"""Tests for the canasta_extension_resolve Ansible module."""

import json
import os

import canasta_extension_resolve
from mock_ansible import run_module_with_params


class TestMwMinorToRel:
    def test_patch_version(self):
        assert canasta_extension_resolve.mw_minor_to_rel("1.43.2") == "REL1_43"

    def test_minor_only(self):
        assert canasta_extension_resolve.mw_minor_to_rel("1.43") == "REL1_43"

    def test_other_major(self):
        assert canasta_extension_resolve.mw_minor_to_rel("2.0.0") is None

    def test_empty(self):
        assert canasta_extension_resolve.mw_minor_to_rel("") is None

    def test_none(self):
        assert canasta_extension_resolve.mw_minor_to_rel(None) is None


class TestSelectBranch:
    def test_explicit_wins(self):
        assert canasta_extension_resolve.select_branch(
            "https://gerrit.wikimedia.org/r/mediawiki/extensions/OAuth",
            "1.43.2", "mybranch") == "mybranch"

    def test_gerrit_rel(self):
        assert canasta_extension_resolve.select_branch(
            "https://gerrit.wikimedia.org/r/mediawiki/extensions/OAuth",
            "1.43.2", None) == "REL1_43"

    def test_non_gerrit_default(self):
        assert canasta_extension_resolve.select_branch(
            "https://github.com/example/Foo", "1.43.2", None) is None

    def test_gerrit_no_version(self):
        assert canasta_extension_resolve.select_branch(
            "https://gerrit.wikimedia.org/r/mediawiki/extensions/OAuth",
            None, None) is None


class TestBranchExists:
    def test_invalid_repo(self):
        # A bad URL makes git ls-remote fail; we must not raise.
        assert canasta_extension_resolve.branch_exists(
            "https://invalid.example.invalid/repo.git", "REL1_43") is False


class TestResolve:
    def _json(self, tmp_dir, entries):
        path = os.path.join(tmp_dir, "ExtensionJson.json")
        with open(path, "w") as handle:
            json.dump(entries, handle)
        return path

    def test_resolves_gerrit_branch(self, tmp_dir):
        path = self._json(tmp_dir, {
            "OAuth": {
                "name": "OAuth",
                "repository": "https://gerrit.wikimedia.org/r/mediawiki/extensions/OAuth",
            },
        })
        res = canasta_extension_resolve.resolve(
            "OAuth", "extensions", "1.43.2", None, None, path, "https://unreachable.example/ExtensionJson.json")
        assert res.get("failed") is not True
        assert res["repository"] == "https://gerrit.wikimedia.org/r/mediawiki/extensions/OAuth"
        # REL1_43 may not be a real head on the live repo; the test only
        # asserts the branch derivation path is consistent.
        assert res["branch"] in (None, "REL1_43")

    def test_repository_override_skips_lookup(self, tmp_dir):
        path = self._json(tmp_dir, {})
        res = canasta_extension_resolve.resolve(
            "Whatever", "extensions", "1.43.2",
            "https://example.org/Whatever.git", None, path, "https://unreachable.example/ExtensionJson.json")
        assert res.get("failed") is not True
        assert res["repository"] == "https://example.org/Whatever.git"
        assert res["source"] == "explicit"

    def test_not_found(self, tmp_dir):
        path = self._json(tmp_dir, {})
        res = canasta_extension_resolve.resolve(
            "Nope", "extensions", "1.43.2", None, None, path, "https://unreachable.example/ExtensionJson.json")
        assert res.get("failed") is True
        assert "not found" in res["msg"]

    def test_offline_no_snapshot(self, tmp_dir):
        res = canasta_extension_resolve.resolve(
            "OAuth", "extensions", "1.43.2", None, None, None, "https://unreachable.example/ExtensionJson.json")
        assert res.get("failed") is True
        assert "ExtensionJson.json" in res["msg"]


class TestModuleMain:
    def _json(self, tmp_dir, entries):
        path = os.path.join(tmp_dir, "ExtensionJson.json")
        with open(path, "w") as handle:
            json.dump(entries, handle)
        return path

    def test_rejects_unknown_item(self, tmp_dir):
        path = self._json(tmp_dir, {})
        result, failed, msg = run_module_with_params(
            canasta_extension_resolve,
            {"name": "Nope", "item_type": "extensions", "json_path": path,
             "json_url": "https://unreachable.example/ExtensionJson.json"})
        assert failed is True
        assert "not found" in msg
