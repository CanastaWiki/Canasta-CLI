"""Tests for the canasta_extension_resolve Ansible module."""

import json
import os

import pytest

import canasta_extension_resolve
from mock_ansible import run_module_with_params

URL = "https://unreachable.example/ExtensionJson.json"
REAL_GERRIT_PROJECTS = canasta_extension_resolve.gerrit_projects


@pytest.fixture(autouse=True)
def no_gerrit(monkeypatch):
    """Keep every test off the network: Gerrit reports no matching project
    unless a test sets its own answer."""
    monkeypatch.setattr(canasta_extension_resolve, "gerrit_projects",
                        lambda name: {})


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

    def test_plain_http_rejected(self):
        assert canasta_extension_resolve.validate_repository_url(
            "http://gitea.internal/example/Foo.git") is not None
        assert canasta_extension_resolve.validate_repository_url(
            "HTTP://github.com/example/Foo.git") is not None

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

    def test_lookup_ssh_never_used(self, tmp_dir):
        # The community-supplied ExtensionJson.json value is untrusted and must
        # be a plain http(s) remote; anything else is not used at all.
        path = self._json(tmp_dir, {
            "Foo": {"name": "Foo", "repository": "ssh://git@example.com/Foo.git"}})
        res = canasta_extension_resolve.resolve(
            "Foo", "extensions", "1.43.2", None, None, path,
            "https://unreachable.example/ExtensionJson.json")
        assert res.get("failed") is True
        assert "ssh://" not in res["msg"]
        assert "not found" in res["msg"]

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


def _json_file(tmp_dir, entries):
    path = os.path.join(tmp_dir, "ExtensionJson.json")
    with open(path, "w") as handle:
        json.dump(entries, handle)
    return path


def _gerrit(monkeypatch, projects):
    calls = []

    def fake(name):
        calls.append(name)
        return projects
    monkeypatch.setattr(canasta_extension_resolve, "gerrit_projects", fake)
    return calls


@pytest.fixture
def verified(monkeypatch):
    monkeypatch.setattr(canasta_extension_resolve, "branch_exists", lambda *a: True)


class TestCaseInsensitiveDataset:
    def test_lowercase_name_resolves_to_the_canonical_key(self, tmp_dir, verified):
        path = _json_file(tmp_dir, {"SemanticMediaWiki": {
            "repository": "https://github.com/SemanticMediaWiki/SemanticMediaWiki"}})
        res = canasta_extension_resolve.resolve(
            "semanticmediawiki", "extensions", "1.43.2", None, None, path, URL)
        assert res["name"] == "SemanticMediaWiki"
        assert res["repository"].endswith("/SemanticMediaWiki")

    def test_ambiguous_case_insensitive_match_is_not_used(self, tmp_dir):
        path = _json_file(tmp_dir, {
            "Foo": {"repository": "https://example.com/Foo"},
            "FOO": {"repository": "https://example.com/FOO"}})
        res = canasta_extension_resolve.resolve(
            "foo", "extensions", "1.43.2", None, None, path, URL)
        assert res.get("failed") is True


class TestGerritFallback:
    def test_phabricator_url_falls_through_to_gerrit(self, tmp_dir, monkeypatch, verified):
        path = _json_file(tmp_dir, {"Cargo": {
            "repository": "https://phabricator.wikimedia.org/diffusion/ECRG/"}})
        _gerrit(monkeypatch, {"mediawiki/extensions/Cargo": {"state": "ACTIVE"}})
        res = canasta_extension_resolve.resolve(
            "cargo", "extensions", "1.43.2", None, None, path, URL)
        assert res["name"] == "Cargo"
        assert res["repository"] == "https://gerrit.wikimedia.org/r/mediawiki/extensions/Cargo"
        assert res["source"] == "gerrit"

    def test_missing_dataset_repository_falls_through_to_gerrit(self, tmp_dir, monkeypatch, verified):
        path = _json_file(tmp_dir, {"PageForms": {"name": "PageForms"}})
        _gerrit(monkeypatch, {
            "mediawiki/extensions/BlueSpicePageFormsConnector": {"state": "ACTIVE"},
            "mediawiki/extensions/PageForms": {"state": "ACTIVE"}})
        res = canasta_extension_resolve.resolve(
            "PageForms", "extensions", "1.43.2", None, None, path, URL)
        assert res["repository"] == "https://gerrit.wikimedia.org/r/mediawiki/extensions/PageForms"

    def test_substring_matches_are_not_accepted(self, tmp_dir, monkeypatch):
        path = _json_file(tmp_dir, {})
        _gerrit(monkeypatch, {
            "mediawiki/extensions/BlueSpicePageFormsConnector": {"state": "ACTIVE"}})
        res = canasta_extension_resolve.resolve(
            "PageForms", "extensions", "1.43.2", None, None, path, URL)
        assert res.get("failed") is True
        assert "not found in ExtensionJson.json or on Wikimedia Gerrit" in res["msg"]


class TestSkins:
    def test_skin_resolves_from_gerrit_case_insensitively(self, tmp_dir, monkeypatch, verified):
        path = _json_file(tmp_dir, {})
        _gerrit(monkeypatch, {"mediawiki/skins/Timeless": {"state": "ACTIVE"}})
        res = canasta_extension_resolve.resolve(
            "timeless", "skins", "1.43.2", None, None, path, URL)
        assert res["name"] == "Timeless"
        assert res["repository"] == "https://gerrit.wikimedia.org/r/mediawiki/skins/Timeless"
        assert res["branch"] == "REL1_43"

    def test_skins_never_use_the_extension_dataset(self, tmp_dir, monkeypatch):
        path = _json_file(tmp_dir, {"Vector": {
            "repository": "https://example.com/an-extension-named-Vector"}})
        _gerrit(monkeypatch, {})
        res = canasta_extension_resolve.resolve(
            "Vector", "skins", "1.43.2", None, None, path, URL)
        assert res.get("failed") is True
        assert "example.com" not in res["msg"]

    def test_archived_skin_is_refused_with_its_new_home(self, tmp_dir, monkeypatch):
        path = _json_file(tmp_dir, {})
        _gerrit(monkeypatch, {"mediawiki/skins/chameleon": {
            "state": "READ_ONLY",
            "description": "[ARCHIVED] Now maintained at\n https://github.com/ProfessionalWiki/chameleon"}})
        res = canasta_extension_resolve.resolve(
            "Chameleon", "skins", "1.43.2", None, None, path, URL)
        assert res.get("failed") is True
        assert "archived on Wikimedia Gerrit" in res["msg"]
        assert "https://github.com/ProfessionalWiki/chameleon" in res["msg"]
        assert "--repository" in res["msg"]

    def test_unknown_skin_asks_for_repository(self, tmp_dir):
        path = _json_file(tmp_dir, {})
        res = canasta_extension_resolve.resolve(
            "Citizen", "skins", "1.43.2", None, None, path, URL)
        assert res["msg"] == ("Skin 'Citizen' is not on Wikimedia Gerrit. Pass "
                              "--repository with its git URL.")

    def test_unreachable_gerrit_asks_for_repository(self, tmp_dir, monkeypatch):
        path = _json_file(tmp_dir, {})
        monkeypatch.setattr(canasta_extension_resolve, "gerrit_projects", lambda n: None)
        res = canasta_extension_resolve.resolve(
            "Timeless", "skins", "1.43.2", None, None, path, URL)
        assert "Could not reach Wikimedia Gerrit" in res["msg"]


class TestNames:
    @pytest.mark.parametrize("name", ["../etc", "a/b", "-x", "a b", ""])
    def test_invalid_names_never_reach_gerrit(self, tmp_dir, monkeypatch, name):
        calls = _gerrit(monkeypatch, {})
        res = canasta_extension_resolve.resolve(
            name, "skins", "1.43.2", None, None, _json_file(tmp_dir, {}), URL)
        assert res.get("failed") is True
        assert calls == []


class TestGerritProjects:
    @pytest.mark.parametrize("prefix", [")]}'\n", ""])
    def test_decodes_the_project_map(self, monkeypatch, prefix):
        body = (prefix + json.dumps(
            {"mediawiki/skins/Vector": {"state": "ACTIVE"}})).encode()
        seen = {}

        class Resp:
            def read(self):
                return body

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_urlopen(request, timeout):
            seen["url"] = request.full_url
            return Resp()
        import urllib.request
        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        projects = REAL_GERRIT_PROJECTS("Vector")
        assert projects == {"mediawiki/skins/Vector": {"state": "ACTIVE"}}
        assert seen["url"] == "https://gerrit.wikimedia.org/r/projects/?m=Vector&d"

    def test_unreachable_returns_none(self, monkeypatch):
        import urllib.request

        def boom(request, timeout):
            raise OSError("no network")
        monkeypatch.setattr(urllib.request, "urlopen", boom)
        assert REAL_GERRIT_PROJECTS("Vector") is None


class TestHttpsOnlyLookups:
    def test_http_dataset_url_falls_through_to_gerrit(self, tmp_dir, monkeypatch, verified):
        path = _json_file(tmp_dir, {"Foo": {
            "repository": "http://example.org/Foo.git"}})
        _gerrit(monkeypatch, {"mediawiki/extensions/Foo": {"state": "ACTIVE"}})
        res = canasta_extension_resolve.resolve(
            "Foo", "extensions", "1.43.2", None, None, path, URL)
        assert res["repository"] == "https://gerrit.wikimedia.org/r/mediawiki/extensions/Foo"
        assert res["source"] == "gerrit"

    def test_http_dataset_url_never_used(self, tmp_dir, monkeypatch):
        path = _json_file(tmp_dir, {"Foo": {
            "repository": "http://example.org/Foo.git"}})
        _gerrit(monkeypatch, {})
        res = canasta_extension_resolve.resolve(
            "Foo", "extensions", "1.43.2", None, None, path, URL)
        assert res.get("failed") is True
        assert "http://" not in res["msg"]

    def test_explicit_http_repository_still_trusted(self, tmp_dir, verified):
        path = _json_file(tmp_dir, {})
        res = canasta_extension_resolve.resolve(
            "Foo", "extensions", "1.43.2", "http://gitea.internal/Foo.git",
            None, path, URL)
        assert res.get("failed") is not True
        assert res["repository"] == "http://gitea.internal/Foo.git"


class TestSourceLabel:
    @pytest.mark.parametrize("source,url,label", [
        ("gerrit", "https://gerrit.wikimedia.org/r/mediawiki/skins/Citizen",
         "Wikimedia Gerrit"),
        ("file:/x/ExtensionJson.json",
         "https://gerrit.wikimedia.org/r/mediawiki/extensions/OAuth",
         "ExtensionJson.json"),
        ("url:https://extjsonuploader.toolforge.org/ExtensionJson.json",
         "https://github.com/example/Foo",
         "ExtensionJson.json; not hosted on Wikimedia Gerrit"),
        ("explicit", "git@example.com:Foo.git",
         "--repository; not hosted on Wikimedia Gerrit"),
        ("explicit", "https://gerrit.wikimedia.org.evil.example/Foo",
         "--repository; not hosted on Wikimedia Gerrit"),
    ])
    def test_labels(self, source, url, label):
        assert canasta_extension_resolve.source_label(source, url) == label

    def test_resolve_returns_label(self, tmp_dir, verified):
        path = _json_file(tmp_dir, {"Foo": {
            "repository": "https://github.com/example/Foo"}})
        res = canasta_extension_resolve.resolve(
            "Foo", "extensions", "1.43.2", None, None, path, URL)
        assert res["source_label"] == (
            "ExtensionJson.json; not hosted on Wikimedia Gerrit")
