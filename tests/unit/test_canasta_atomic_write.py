"""A failed .env write must leave the previous file whole."""

import errno
import os
import stat
import subprocess

import pytest

import canasta_atomic_write
import canasta_env
from mock_ansible import run_module_with_params


def _no_space(*_args, **_kwargs):
    raise OSError(errno.ENOSPC, "No space left on device")


def _mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


class TestReplaceFile:
    def test_replaces_content_and_keeps_mode(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text("A=1\n")
        os.chmod(env, 0o600)
        canasta_atomic_write.replace_file(str(env), "B=2\n")
        assert env.read_text() == "B=2\n"
        assert _mode(env) == 0o600

    def test_new_file_gets_the_umask_mode(self, tmp_path):
        env = tmp_path / ".env"
        old = os.umask(0o022)
        try:
            canasta_atomic_write.replace_file(str(env), "A=1\n")
        finally:
            os.umask(old)
        assert env.read_text() == "A=1\n"
        assert _mode(env) == 0o644

    def test_failed_write_leaves_the_original(self, tmp_path, monkeypatch):
        env = tmp_path / ".env"
        env.write_text("MYSQL_PASSWORD=secret\n")
        monkeypatch.setattr(canasta_atomic_write.os, "fsync", _no_space)
        with pytest.raises(OSError):
            canasta_atomic_write.replace_file(str(env), "")
        assert env.read_text() == "MYSQL_PASSWORD=secret\n"
        assert sorted(p.name for p in tmp_path.iterdir()) == [".env"]


class TestModuleWrite:
    def test_set_reports_a_failed_write_and_keeps_the_file(
            self, sample_env_file, monkeypatch):
        with open(sample_env_file) as f:
            before = f.read()
        monkeypatch.setattr(canasta_env, "replace_file", _no_space)
        _, failed, msg = run_module_with_params(canasta_env, {
            "path": sample_env_file, "state": "set",
            "key": "NEW_KEY", "value": "v", "keys": None,
        })
        assert failed
        assert "No space left" in msg
        with open(sample_env_file) as f:
            assert f.read() == before


class TestRemoteScript:
    def _run(self, path, content, env=None):
        return subprocess.run(
            ["sh", "-c", canasta_atomic_write.REMOTE_REPLACE_SCRIPT, "sh",
             str(path)],
            input=content, text=True, capture_output=True, env=env,
        )

    def test_replaces_content_and_keeps_mode(self, tmp_path):
        env = tmp_path / "my dir" / ".env"
        env.parent.mkdir()
        env.write_text("A=1\n")
        os.chmod(env, 0o600)
        assert self._run(env, "B=2\n").returncode == 0
        assert env.read_text() == "B=2\n"
        assert _mode(env) == 0o600
        assert sorted(p.name for p in env.parent.iterdir()) == [".env"]

    def test_failed_write_leaves_the_original(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text("MYSQL_PASSWORD=secret\n")
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        fake_cat = bin_dir / "cat"
        fake_cat.write_text("#!/bin/sh\necho 'No space left on device' >&2\nexit 1\n")
        fake_cat.chmod(0o755)
        path = "%s:%s" % (bin_dir, os.environ["PATH"])
        result = self._run(env, "", env=dict(os.environ, PATH=path))
        assert result.returncode != 0
        assert env.read_text() == "MYSQL_PASSWORD=secret\n"
        assert sorted(p.name for p in tmp_path.iterdir()) == [".env", "bin"]
