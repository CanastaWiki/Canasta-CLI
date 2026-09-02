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

    def test_non_gerrit_rel_attempted(self):
        # REL branches are attempted on every remote (GitHub/GitLab mirrors
        # carry them too); the caller falls back when the branch is missing.
        assert canasta_extension_resolve.select_branch(
            "https://github.com/example/Foo", "1.43.2", None) == "REL1_43"

    def test_no_version_default(self):
        assert canasta_extension_resolve.select_branch(
            "https://github.com/example/Foo", None, None) is None

    def test_gerrit_no_version(self):
        assert canasta_extension_resolve.select_branch(
            "https://gerrit.wikimedia.org/r/mediawiki/extensions/OAuth",
            None, None) is None


class TestValidateRepositoryUrl:
    def test_https_ok(self):
        assert canasta_extension_resolve.validate_repository_url(
            "https://github.com/example/Foo.git") is None

    def test_http_ok(self):
        assert canasta_extension_resolve.validate_repository_url(
            "http://gitea.internal/example/Foo.git") is None

    def test_ext_transport_rejected(self):
        assert canasta_extension_resolve.validate_repository_url(
            "ext::sh -c touch /tmp/pwned") is not None

    def test_ssh_transport_rejected(self):
        assert canasta_extension_resolve.validate_repository_url(
            "ssh://git@example.com/Foo.git") is not None

    def test_file_scheme_rejected(self):
        assert canasta_extension_resolve.validate_repository_url(
            "file:///tmp/Foo") is not None

    def test_leading_dash_rejected(self):
        assert canasta_extension_resolve.validate_repository_url(
            "-ufoo=https://example.com/pwned") is not None

    def test_empty_rejected(self):
        assert canasta_extension_resolve.validate_repository_url("") is not None
        assert canasta_extension_resolve.validate_repository_url(None) is not None


class TestBranchExists:
    def test_invalid_repo(self):
        # A bad URL makes git ls-remote fail; we must not raise, and the
        # result is None ("unknown"), never False ("confirmed absent").
        assert canasta_extension_resolve.branch_exists(
            "https://invalid.example.invalid/repo.git", "REL1_43") is None


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

    def test_explicit_ssh_override_allowed(self, tmp_dir):
        # A trusted operator-provided --repository may be an internal ssh://
        # or git@ host; the strict http(s) check must be skipped for it.
        path = self._json(tmp_dir, {})
        res = canasta_extension_resolve.resolve(
            "Whatever", "extensions", "1.43.2",
            "ssh://git@github.com/example/Whatever.git", None, path,
            "https://unreachable.example/ExtensionJson.json")
        assert res.get("failed") is not True
        assert res["repository"] == "ssh://git@github.com/example/Whatever.git"
        assert res["source"] == "explicit"

    def test_lookup_ssh_rejected(self, tmp_dir):
        # The community-supplied ExtensionJson.json value is untrusted and must
        # remain a plain http(s) remote.
        path = self._json(tmp_dir, {
            "Foo": {"name": "Foo", "repository": "ssh://git@example.com/Foo.git"}})
        res = canasta_extension_resolve.resolve(
            "Foo", "extensions", "1.43.2", None, None, path,
            "https://unreachable.example/ExtensionJson.json")
        assert res.get("failed") is True
        assert "http(s)" in res["msg"]

    def test_explicit_leading_dash_rejected(self, tmp_dir):
        # Injection-style URLs are refused even on the trusted override path.
        path = self._json(tmp_dir, {})
        res = canasta_extension_resolve.resolve(
            "Whatever", "extensions", "1.43.2",
            "-ufoo=https://example.com/pwned", None, path,
            "https://unreachable.example/ExtensionJson.json")
        assert res.get("failed") is True

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

    def test_resolve_keeps_rel_when_unverified(self, tmp_dir, monkeypatch):
        # No network to the remote from this host: branch_exists -> None
        # ("unknown"), so we must keep REL1_43 rather than fall back to master.
        monkeypatch.setattr(canasta_extension_resolve, "branch_exists", lambda *a: None)
        path = self._json(tmp_dir, {
            "OAuth": {"name": "OAuth",
                      "repository": "https://gerrit.wikimedia.org/r/mediawiki/extensions/OAuth"}})
        res = canasta_extension_resolve.resolve(
            "OAuth", "extensions", "1.43.2", None, None, path,
            "https://unreachable.example/ExtensionJson.json")
        assert res.get("failed") is not True
        assert res["branch"] == "REL1_43"
        assert res.get("branch_unverified") is True

    def test_resolve_falls_back_when_absent(self, tmp_dir, monkeypatch):
        # ls-remote confirms the branch is genuinely absent -> default branch.
        monkeypatch.setattr(canasta_extension_resolve, "branch_exists", lambda *a: False)
        path = self._json(tmp_dir, {
            "OAuth": {"name": "OAuth",
                      "repository": "https://gerrit.wikimedia.org/r/mediawiki/extensions/OAuth"}})
        res = canasta_extension_resolve.resolve(
            "OAuth", "extensions", "1.43.2", None, None, path,
            "https://unreachable.example/ExtensionJson.json")
        assert res.get("failed") is not True
        assert res["branch"] is None
        assert res.get("branch_unverified") is False

    def test_resolve_uses_rel_when_verified(self, tmp_dir, monkeypatch):
        monkeypatch.setattr(canasta_extension_resolve, "branch_exists", lambda *a: True)
        path = self._json(tmp_dir, {
            "OAuth": {"name": "OAuth",
                      "repository": "https://gerrit.wikimedia.org/r/mediawiki/extensions/OAuth"}})
        res = canasta_extension_resolve.resolve(
            "OAuth", "extensions", "1.43.2", None, None, path,
            "https://unreachable.example/ExtensionJson.json")
        assert res.get("failed") is not True
        assert res["branch"] == "REL1_43"
        assert res.get("branch_unverified") is False


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
