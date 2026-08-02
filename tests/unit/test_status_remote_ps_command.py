"""`canasta status` on a remote host must send a command, not a list.

`ps_cmd` is built as a list (`compose_cmd + ["ps"]`). Interpolating it
into the remote command with %s stringified the list itself, so the
target shell received:

    cd /srv/wiki && ['podman-compose', 'ps']

and reported:

    [podman-compose,: command not found

Only the remote branch was affected — the local branch passes the list
straight to subprocess, where a list is what is wanted. That asymmetry is
why it survived: every local test passed.
"""

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

import direct_commands  # noqa: E402
import direct_commands._helpers  # noqa: E402
import direct_commands.info  # noqa: E402


def _stub_common(monkeypatch, inst, sent):
    monkeypatch.setattr(direct_commands.info, "_resolve_status_instance",
                        lambda args: ("wiki", inst))
    monkeypatch.setattr(direct_commands._helpers, "_check_running_compose",
                        lambda *a, **kw: True)
    monkeypatch.setattr(
        direct_commands._helpers, "_ssh_run",
        lambda host, cmd, **kw: (sent.append((host, cmd)), (0, ""))[1])


REMOTE = {
    "path": "/srv/wiki",
    "host": "node1.example.com",
    "orchestrator": "compose",
    "composeCommand": "podman-compose",
    "inspectCommand": "podman",
}


class TestTheRemoteCommandIsAString:
    def test_the_list_is_not_stringified(self, monkeypatch, capsys):
        sent = []
        _stub_common(monkeypatch, REMOTE, sent)

        direct_commands.cmd_status(type("Args", (), {"id": "wiki"})())
        capsys.readouterr()

        assert sent, "no remote command was sent"
        _host, cmd = sent[0]
        # Not "no quotes at all": _shell_quote legitimately quotes the
        # path. These two are the signature of a stringified list.
        assert "['" not in cmd and "', '" not in cmd, (
            "the ps command list reached the remote shell as its Python "
            "repr, which the shell reads as a filename: %r" % cmd
        )

    def test_the_compose_command_survives_intact(self, monkeypatch, capsys):
        sent = []
        _stub_common(monkeypatch, REMOTE, sent)

        direct_commands.cmd_status(type("Args", (), {"id": "wiki"})())
        capsys.readouterr()

        _host, cmd = sent[0]
        assert "podman-compose ps" in cmd

    def test_the_path_is_quoted_and_present(self, monkeypatch, capsys):
        sent = []
        _stub_common(monkeypatch, REMOTE, sent)

        direct_commands.cmd_status(type("Args", (), {"id": "wiki"})())
        capsys.readouterr()

        _host, cmd = sent[0]
        assert cmd.startswith("cd ")
        assert "/srv/wiki" in cmd

    def test_docker_compose_is_joined_too(self, monkeypatch, capsys):
        # The default runtime is two tokens, so a naive join bug would
        # show up here as well.
        inst = dict(REMOTE)
        inst.pop("composeCommand")
        inst.pop("inspectCommand")
        sent = []
        _stub_common(monkeypatch, inst, sent)

        direct_commands.cmd_status(type("Args", (), {"id": "wiki"})())
        capsys.readouterr()

        _host, cmd = sent[0]
        assert "docker compose ps" in cmd


class TestTheLocalPathStillPassesAList:
    def test_local_status_uses_argv_not_a_string(self, monkeypatch, capsys):
        # subprocess without a shell needs the list form; "fixing" both
        # branches the same way would break this one.
        captured = {}

        def fake_run(cmd, **kw):
            captured["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        inst = {"path": "/srv/wiki", "host": "localhost",
                "orchestrator": "compose"}
        monkeypatch.setattr(direct_commands.info,
                            "_resolve_status_instance",
                            lambda args: ("wiki", inst))
        monkeypatch.setattr(direct_commands._helpers,
                            "_check_running_compose", lambda *a, **kw: True)
        monkeypatch.setattr(direct_commands.info.subprocess, "run", fake_run)

        direct_commands.cmd_status(type("Args", (), {"id": "wiki"})())
        capsys.readouterr()

        assert isinstance(captured["cmd"], list)
        assert captured["cmd"][-1] == "ps"
