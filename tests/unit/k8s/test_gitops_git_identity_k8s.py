"""gitops init on a Kubernetes instance keeps the git identity in the
instance's repository: the flags are stored verbatim in the repository's own
config, never in the host's global git config, and the identity check accepts
the flags or an identity the host already has."""

import subprocess

import pytest

from test_gitops_git_identity import CHECK, EMAIL, NAME, STORE, _config, _play, _tasks

K8S_FILES = ["init_kubernetes.yml"]


@pytest.mark.parametrize("flow", K8S_FILES)
def test_identity_is_stored_verbatim_in_the_repository_only(tmp_path, flow):
    inst = tmp_path / "inst"
    inst.mkdir()
    subprocess.run(["git", "init", "-q", str(inst)], check=True)
    proc, home = _play(tmp_path, _tasks(flow, CHECK + STORE), {
        "instance_path": str(inst), "git_user_name": NAME,
        "git_user_email": EMAIL})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _config(inst / ".git" / "config", "user.name") == NAME
    assert _config(inst / ".git" / "config", "user.email") == EMAIL
    assert not (home / ".gitconfig").exists()


@pytest.mark.parametrize("flow", K8S_FILES)
def test_check_refuses_without_flags_or_an_identity(tmp_path, flow):
    proc, _ = _play(tmp_path, _tasks(flow, CHECK), {})
    assert proc.returncode != 0
    assert "Pass --git-user-name and --git-user-email" in proc.stdout + proc.stderr


@pytest.mark.parametrize("flow", K8S_FILES)
def test_check_accepts_an_identity_the_host_already_has(tmp_path, flow):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".gitconfig").write_text(
        "[user]\n\tname = Operator\n\temail = op@example.com\n")
    proc, _ = _play(tmp_path, _tasks(flow, CHECK), {})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _config(home / ".gitconfig", "user.name") == "Operator"
