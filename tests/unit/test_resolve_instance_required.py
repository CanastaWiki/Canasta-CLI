"""resolve_instance() must stay silent for best-effort callers.

The failure this pins: resolve_instance() printed a user-facing "Error:"
line and then exited, and the opportunistic container-runtime lookup in
build_ansible_args() caught the SystemExit to carry on. Catching the exit
does not unprint the message, so any command run outside an instance
directory without --id emitted a spurious

    Error: no instance found for current directory

before doing its work — misleading on its own, and actively wrong when
the command later failed for an unrelated reason, since the stray line
reads as the cause.
"""

import os
import sys

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, REPO_ROOT)

import canasta  # noqa: E402


class TestResolveInstanceRequired:
    def test_returns_none_and_stays_quiet_when_not_required(
        self, tmp_path, monkeypatch, capsys
    ):
        """No registry at all: the common case on a fresh controller."""
        monkeypatch.setenv("CANASTA_CONFIG_DIR", str(tmp_path))

        assert canasta.resolve_instance(required=False) is None
        captured = capsys.readouterr()
        assert captured.err == ""
        assert captured.out == ""

    def test_returns_none_when_cwd_is_not_an_instance(
        self, tmp_path, monkeypatch, capsys
    ):
        """Registry exists, but nothing matches the working directory."""
        monkeypatch.setenv("CANASTA_CONFIG_DIR", str(tmp_path))
        (tmp_path / "conf.json").write_text('{"Instances": {}}')
        workdir = tmp_path / "somewhere-else"
        workdir.mkdir()
        monkeypatch.chdir(workdir)
        monkeypatch.delenv("CANASTA_HOST_PWD", raising=False)

        assert canasta.resolve_instance(required=False) is None
        assert capsys.readouterr().err == ""

    def test_unknown_id_is_quiet_when_not_required(
        self, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.setenv("CANASTA_CONFIG_DIR", str(tmp_path))
        (tmp_path / "conf.json").write_text('{"Instances": {}}')

        assert canasta.resolve_instance("nosuch", required=False) is None
        assert capsys.readouterr().err == ""

    def test_still_reports_and_exits_when_required(
        self, tmp_path, monkeypatch, capsys
    ):
        """The default must keep today's behavior for real callers."""
        import pytest

        monkeypatch.setenv("CANASTA_CONFIG_DIR", str(tmp_path))
        (tmp_path / "conf.json").write_text('{"Instances": {}}')
        workdir = tmp_path / "somewhere-else"
        workdir.mkdir()
        monkeypatch.chdir(workdir)
        monkeypatch.delenv("CANASTA_HOST_PWD", raising=False)

        with pytest.raises(SystemExit) as exc:
            canasta.resolve_instance()
        assert exc.value.code == 1
        assert "no instance found for current directory" in capsys.readouterr().err

    def test_resolves_normally_when_the_instance_exists(
        self, tmp_path, monkeypatch
    ):
        """required=False must not change a successful lookup."""
        monkeypatch.setenv("CANASTA_CONFIG_DIR", str(tmp_path))
        (tmp_path / "conf.json").write_text(
            '{"Instances": {"mysite": {"path": "/srv/mysite",'
            ' "orchestrator": "compose"}}}'
        )

        inst = canasta.resolve_instance("mysite", required=False)
        assert inst is not None
        assert inst["id"] == "mysite"
        assert inst["path"] == "/srv/mysite"


class TestRuntimeLookupIsBestEffort:
    def test_build_ansible_args_does_not_catch_systemexit(self):
        """The runtime lookup must ask for a quiet failure, not swallow one.

        `except SystemExit` around the lookup would suppress the exit but
        leave the printed error behind, which is the bug this fixes.
        """
        source = open(os.path.join(REPO_ROOT, "canasta.py")).read()
        marker = 'if "compose_command" not in extra_vars:'
        assert marker in source
        block = source[source.index(marker):]
        block = block[:block.index("vars_file = tempfile")]
        assert "required=False" in block, (
            "the opportunistic runtime lookup must call resolve_instance "
            "with required=False"
        )
        assert "except SystemExit" not in block, (
            "catching SystemExit hides the exit but not the error message "
            "resolve_instance already printed"
        )
