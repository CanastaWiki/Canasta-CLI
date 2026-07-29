"""Tests for `canasta fix-sysctl` (#1284)."""

import json
import os
import subprocess
import sys


REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, REPO_ROOT)

import direct_commands  # noqa: E402


def _args(**kw):
    defaults = {"id": None, "host": None}
    defaults.update(kw)
    return type("Args", (), defaults)()


def _fake_subprocess(stdout_text, rc=0):
    """Return a function that simulates subprocess.run with given output."""
    def fake_run(cmd, **kw):
        return type("Result", (), {
            "returncode": rc,
            "stdout": stdout_text,
            "stderr": "",
        })()
    return fake_run


class TestFixSysctlRegistered:
    def test_registered_as_direct(self):
        assert direct_commands.is_direct_command("fix_sysctl")


class TestFixSysctlLocalAlreadyFixed:
    def test_returns_zero_when_port_floor_is_80(self, monkeypatch, capsys):
        monkeypatch.setattr(
            subprocess, "run",
            _fake_subprocess("PORT_FLOOR:80\n"),
        )
        rc = direct_commands.cmd_fix_sysctl(_args())
        assert rc == 0
        out = capsys.readouterr().out
        assert "nothing to fix" in out.lower()

    def test_returns_zero_when_port_floor_is_below_80(self, monkeypatch, capsys):
        monkeypatch.setattr(
            subprocess, "run",
            _fake_subprocess("PORT_FLOOR:1\n"),
        )
        rc = direct_commands.cmd_fix_sysctl(_args())
        assert rc == 0
        out = capsys.readouterr().out
        assert "nothing to fix" in out.lower()


class TestFixSysctlLocalFullSuccess:
    def test_returns_zero_with_success_message(self, monkeypatch, capsys):
        monkeypatch.setattr(
            subprocess, "run",
            _fake_subprocess(
                "PORT_FLOOR:1024\nSYSCTL_OK\nPERSIST_OK\n"
            ),
        )
        rc = direct_commands.cmd_fix_sysctl(_args())
        assert rc == 0
        out = capsys.readouterr().out
        assert "Fixed unprivileged port floor" in out
        assert "Runtime:" in out
        assert "Persistent:" in out


class TestFixSysctlLocalSysctlFailed:
    def test_returns_one_when_sysctl_fails(self, monkeypatch, capsys):
        monkeypatch.setattr(
            subprocess, "run",
            _fake_subprocess("PORT_FLOOR:1024\nSYSCTL_FAILED\n"),
        )
        rc = direct_commands.cmd_fix_sysctl(_args())
        assert rc == 1
        err = capsys.readouterr().err
        assert "Could not set" in err
        assert "passwordless sudo" in err


class TestFixSysctlLocalPersistFailed:
    def test_returns_one_when_persist_fails(self, monkeypatch, capsys):
        monkeypatch.setattr(
            subprocess, "run",
            _fake_subprocess(
                "PORT_FLOOR:1024\nSYSCTL_OK\nPERSIST_FAILED\n"
            ),
        )
        rc = direct_commands.cmd_fix_sysctl(_args())
        assert rc == 1
        out = capsys.readouterr().out
        assert "Runtime fix applied" in out
        assert "could not write" in out


class TestFixSysctlLocalUnknownPortFloor:
    def test_returns_one_when_cannot_read_port_floor(self, monkeypatch, capsys):
        monkeypatch.setattr(
            subprocess, "run",
            _fake_subprocess("PORT_FLOOR:?\n"),
        )
        rc = direct_commands.cmd_fix_sysctl(_args())
        assert rc == 1
        err = capsys.readouterr().err
        assert "Could not read" in err
        assert "not Linux" in err

    def test_returns_one_on_empty_output(self, monkeypatch, capsys):
        monkeypatch.setattr(
            subprocess, "run",
            _fake_subprocess(""),
        )
        rc = direct_commands.cmd_fix_sysctl(_args())
        assert rc == 1


class TestFixSysctlLocalSubprocessError:
    def test_returns_one_on_timeout(self, monkeypatch, capsys):
        def fake_timeout(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd, 30)

        monkeypatch.setattr(subprocess, "run", fake_timeout)
        rc = direct_commands.cmd_fix_sysctl(_args())
        assert rc == 1

    def test_returns_one_on_oserror(self, monkeypatch, capsys):
        def fake_oserror(cmd, **kw):
            raise OSError("No such file or directory")

        monkeypatch.setattr(subprocess, "run", fake_oserror)
        rc = direct_commands.cmd_fix_sysctl(_args())
        assert rc == 1


class TestFixSysctlRemote:
    def test_remote_success(self, monkeypatch, capsys):
        monkeypatch.setattr(
            direct_commands._helpers, "_ssh_run",
            lambda host, cmd: (0, "PORT_FLOOR:1024\nSYSCTL_OK\nPERSIST_OK\n"),
        )
        rc = direct_commands.cmd_fix_sysctl(_args(host="prod1.example.com"))
        assert rc == 0
        out = capsys.readouterr().out
        assert "prod1.example.com" in out

    def test_remote_ssh_failure(self, monkeypatch, capsys):
        monkeypatch.setattr(
            direct_commands._helpers, "_ssh_run",
            lambda host, cmd: (255, ""),
        )
        rc = direct_commands.cmd_fix_sysctl(_args(host="prod1.example.com"))
        assert rc == 1
        err = capsys.readouterr().err
        assert "failed to connect" in err

    def test_remote_ssh_empty_stdout(self, monkeypatch, capsys):
        monkeypatch.setattr(
            direct_commands._helpers, "_ssh_run",
            lambda host, cmd: (1, ""),
        )
        rc = direct_commands.cmd_fix_sysctl(_args(host="prod1.example.com"))
        assert rc == 1
        err = capsys.readouterr().err
        assert "failed to connect" in err


class TestFixSysctlWithInstanceId:
    def test_resolves_host_from_registry(self, monkeypatch, capsys, tmp_path):
        monkeypatch.setenv("CANASTA_CONFIG_DIR", str(tmp_path))
        conf = {
            "Instances": {
                "mysite": {
                    "id": "mysite",
                    "path": str(tmp_path / "mysite"),
                    "orchestrator": "compose",
                    "host": "myserver.example.com",
                }
            }
        }
        os.makedirs(tmp_path / "mysite", exist_ok=True)
        with open(tmp_path / "conf.json", "w") as f:
            json.dump(conf, f)

        monkeypatch.setattr(
            direct_commands._helpers, "_ssh_run",
            lambda host, cmd: (0, "PORT_FLOOR:1024\nSYSCTL_OK\nPERSIST_OK\n"),
        )
        rc = direct_commands.cmd_fix_sysctl(_args(id="mysite"))
        assert rc == 0
        out = capsys.readouterr().out
        assert "myserver.example.com" in out

    def test_unknown_instance_returns_error(self, monkeypatch, capsys, tmp_path):
        monkeypatch.setenv("CANASTA_CONFIG_DIR", str(tmp_path))
        conf = {"Instances": {}}
        with open(tmp_path / "conf.json", "w") as f:
            json.dump(conf, f)

        rc = direct_commands.cmd_fix_sysctl(_args(id="nonexistent"))
        assert rc == 1
        err = capsys.readouterr().err
        assert "not found in registry" in err
