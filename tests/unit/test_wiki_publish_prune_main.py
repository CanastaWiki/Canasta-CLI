"""wiki_publish.main() --prune dispatch and exit code."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..",
                                "scripts"))
import wiki_publish as wp  # noqa: E402


PAGES = [("CLI:canasta create", "body"), ("CLI:canasta start", "body")]


class _Client:
    """MediaWikiClient stand-in: every page is already up to date."""

    def __init__(self, existing, delete_exc=None):
        self.existing = existing
        self.delete_exc = delete_exc
        self.deleted = []
        self.edits = []

    def fetch_page_texts(self, titles):
        return {wp._normalize_title(t): c for t, c in PAGES}

    def edit_page(self, title, content, summary):
        self.edits.append(title)

    def resolve_namespace_id(self, name):
        return 3000

    def list_pages_in_namespace(self, ns_id):
        return list(self.existing)

    def delete_page(self, title, reason):
        if self.delete_exc is not None:
            raise self.delete_exc
        self.deleted.append(title)


def _run(monkeypatch, client, *extra):
    monkeypatch.setattr(wp, "load_definitions", lambda: {})
    monkeypatch.setattr(wp, "generate_all_pages", lambda data: PAGES)
    monkeypatch.setattr(wp, "MediaWikiClient", lambda *a, **k: client)
    monkeypatch.setattr(wp.time, "sleep", lambda s: None)
    monkeypatch.setattr(sys, "argv", [
        "wiki_publish.py", "--api", "https://wiki.example.com/api.php",
        "--user", "bot", "--pass", "secret", *extra,
    ])
    try:
        wp.main()
    except SystemExit as e:
        return e.code
    return 0


EXISTING = ["CLI:Canasta create", "CLI:Canasta start", "CLI:Canasta gone"]


class TestMainPrune:
    def test_prune_deletes_orphans_and_exits_0(self, monkeypatch, capsys):
        client = _Client(EXISTING)
        assert _run(monkeypatch, client, "--prune") == 0
        assert client.deleted == ["CLI:Canasta gone"]
        out = capsys.readouterr().out
        assert "Deleted orphan CLI:Canasta gone" in out
        assert "Done: 0 page(s) published, 2 unchanged" in out

    def test_without_prune_nothing_is_deleted(self, monkeypatch):
        client = _Client(EXISTING)
        assert _run(monkeypatch, client) == 0
        assert client.deleted == []

    @pytest.mark.parametrize("exc", [
        RuntimeError("API error: boom"),
        wp.PermissionDeniedError("API error: permissiondenied: no"),
    ])
    def test_prune_error_exits_1(self, monkeypatch, capsys, exc):
        client = _Client(EXISTING, delete_exc=exc)
        assert _run(monkeypatch, client, "--prune") == 1
        captured = capsys.readouterr()
        assert "Failed to prune 1 orphaned page(s)" in captured.err
        assert "Failed to publish" not in captured.err
        assert "Done:" not in captured.out

    def test_prune_safety_refusal_exits_1(self, monkeypatch, capsys):
        existing = ["CLI:Canasta orphan %d" % i for i in range(20)]
        client = _Client(existing)
        assert _run(monkeypatch, client, "--prune") == 1
        assert client.deleted == []
        err = capsys.readouterr().err
        assert "refusing" in err
        assert "Failed to prune 1 orphaned page(s)" in err
