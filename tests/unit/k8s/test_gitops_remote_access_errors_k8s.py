"""The Kubernetes gitops init probes the deploy key with `git ls-remote`. Only
a refused key leads to "register this public key"; a changed host key or a
connection error fails with ssh's own error, since registering the key
cannot fix it."""

import base64

import pytest

from test_gitops_remote_access_errors import (
    DENIED,
    DNS,
    HOST_KEY_CHANGED,
    HOST_KEY_FAILED,
    NOT_FOUND,
    PROBE,
    PUBKEY,
    _run,
)


def _probe(tmp_path, stderr, rc, repo="git@github.com:org/repo.git"):
    return _run(tmp_path, PROBE, stderr, rc, {
        "repo": repo,
        "ssh_key": "",
        "_ssh_key_target_path": str(tmp_path / "key"),
        "_gitops_ssh_pubkey": {
            "content": base64.b64encode(PUBKEY.encode()).decode()},
        "_existing_is_ssh_key": True,
        "non_interactive": True,
    })


class TestK8sDeployKeyProbe:
    def test_authorized_key_skips_the_prompt(self, tmp_path):
        proc, out = _probe(tmp_path, "", 0)
        assert proc.returncode == 0, out
        assert "RESULT=ok" in out
        assert PUBKEY not in out

    @pytest.mark.parametrize("stderr", [DENIED, NOT_FOUND])
    def test_refused_key_asks_for_registration(self, tmp_path, stderr):
        proc, out = _probe(tmp_path, stderr, 128)
        assert proc.returncode != 0
        assert "not authorized" in out
        assert PUBKEY in out

    def test_changed_host_key_names_host_and_fix(self, tmp_path):
        proc, out = _probe(tmp_path, HOST_KEY_CHANGED, 128,
                           repo="ssh://git@git.example.com:2222/org/repo.git")
        assert proc.returncode != 0
        assert "ssh-keygen -R '[git.example.com]:2222'" in out
        assert "REMOTE HOST IDENTIFICATION HAS CHANGED" in out
        assert "not authorized" not in out
        assert PUBKEY not in out

    def test_host_key_verification_failure(self, tmp_path):
        proc, out = _probe(tmp_path, HOST_KEY_FAILED, 128)
        assert proc.returncode != 0
        assert "ssh-keygen -R 'github.com'" in out
        assert PUBKEY not in out

    def test_connection_error_shows_ssh_error(self, tmp_path):
        proc, out = _probe(tmp_path, DNS, 128)
        assert proc.returncode != 0
        assert "Could not resolve hostname git.example.com" in out
        assert "not authorized" not in out
        assert PUBKEY not in out
