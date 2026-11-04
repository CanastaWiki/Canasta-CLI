"""`canasta config get` refuses while a gitops pull is unfinished, like the
other commands that read the instance's configuration."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

import direct_commands  # noqa: E402
import direct_commands._helpers  # noqa: E402


def _run(monkeypatch, path):
    monkeypatch.setattr(
        direct_commands._helpers, "_resolve_instance",
        lambda args: ("test", {"path": str(path), "host": "localhost"}))
    monkeypatch.setattr(
        direct_commands._helpers, "_read_env_file",
        lambda *a: {"HTTP_PORT": "80"})
    monkeypatch.setattr(
        direct_commands._helpers, "_read_env_content", lambda *a: "")
    args = type("Args", (), {
        "id": "test", "keys": [], "show_secrets": False})()
    return direct_commands.cmd_config_get(args)


@pytest.mark.parametrize("marker", ["rebase-merge", "rebase-apply"])
def test_refuses_during_an_unfinished_pull(monkeypatch, capsys, tmp_path,
                                           marker):
    (tmp_path / ".git" / marker).mkdir(parents=True)
    assert _run(monkeypatch, tmp_path) == 1
    out = capsys.readouterr()
    assert "HTTP_PORT" not in out.out
    assert "canasta gitops resolve -i test --continue" in out.err


def test_answers_when_no_pull_is_in_progress(monkeypatch, capsys, tmp_path):
    (tmp_path / ".git").mkdir()
    assert _run(monkeypatch, tmp_path) == 0
    assert "HTTP_PORT=80" in capsys.readouterr().out
