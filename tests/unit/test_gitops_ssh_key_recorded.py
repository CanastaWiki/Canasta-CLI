"""A gitops command's --ssh-key is remembered for the instance.

Without it, every later gitops command needed --ssh-key again, and status,
push and pull failed to reach the remote on a host that authenticates with a
key file rather than a forwarded ssh-agent.
"""
import os
import subprocess
import sys

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO_ROOT)

import direct_commands  # noqa: E402

GITOPS = os.path.join(REPO_ROOT, "roles", "gitops")
SSH_KEY_TASKS = os.path.join(GITOPS, "tasks", "_ssh_key.yml")
FLOWS = ("init", "join", "push", "pull", "fix_submodules", "diff")


def _play(tmp_path, inst, extra_vars=None, k8s=False):
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {"instance_path": str(inst), "instance_is_k8s": k8s,
                 "ansible_python_interpreter": sys.executable},
        "tasks": [
            {"ansible.builtin.include_tasks": SSH_KEY_TASKS},
            {"ansible.builtin.debug": {
                "msg": "KEY=[{{ ssh_key | default('') }}]"}},
        ],
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_CONFIG=os.path.join(REPO_ROOT, "ansible.cfg"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    cmd = [playbook, "-i", "localhost,", str(play)]
    for k, v in (extra_vars or {}).items():
        cmd += ["-e", "%s=%s" % (k, v)]
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env,
                          cwd=REPO_ROOT)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc.stdout


class TestTheKeyIsRecordedAndRecalled:
    def test_a_passed_key_is_recorded(self, tmp_path):
        out = _play(tmp_path, tmp_path, {"ssh_key": "/home/u/.ssh/deploy"})
        assert "KEY=[/home/u/.ssh/deploy]" in out
        recorded = tmp_path / ".gitops-ssh-key"
        assert recorded.read_text() == "/home/u/.ssh/deploy\n"
        assert oct(recorded.stat().st_mode & 0o777) == "0o600"

    def test_a_later_command_uses_the_recorded_key(self, tmp_path):
        (tmp_path / ".gitops-ssh-key").write_text("~/.ssh/deploy\n")
        assert "KEY=[~/.ssh/deploy]" in _play(tmp_path, tmp_path)

    def test_passing_a_key_again_replaces_the_recorded_one(self, tmp_path):
        (tmp_path / ".gitops-ssh-key").write_text("/old\n")
        assert "KEY=[/new]" in _play(tmp_path, tmp_path, {"ssh_key": "/new"})
        assert (tmp_path / ".gitops-ssh-key").read_text() == "/new\n"

    def test_kubernetes_keeps_its_deploy_key(self, tmp_path):
        # Its later commands authenticate with the generated deploy key,
        # which Argo CD uses too.
        _play(tmp_path, tmp_path, {"ssh_key": "/home/u/.ssh/op"}, k8s=True)
        assert not (tmp_path / ".gitops-ssh-key").exists()
        (tmp_path / ".gitops-ssh-key").write_text("/home/u/.ssh/op\n")
        assert "KEY=[]" in _play(tmp_path, tmp_path, k8s=True)

    def test_without_a_recorded_key_nothing_changes(self, tmp_path):
        assert "KEY=[]" in _play(tmp_path, tmp_path)
        assert not (tmp_path / ".gitops-ssh-key").exists()


class TestEveryRemoteFlowUsesIt:
    def test_each_flow_recalls_the_key_right_after_resolving(self, tmp_path):
        for flow in FLOWS:
            with open(os.path.join(GITOPS, "tasks", flow + ".yml")) as fh:
                names = [t.get("name") for t in yaml.safe_load(fh)]
            i = names.index("Resolve instance")
            assert names[i + 1] == "Remember or recall the gitops SSH key", flow

    def test_the_recorded_path_is_never_committed(self):
        with open(os.path.join(GITOPS, "files", "gitignore.default")) as fh:
            assert ".gitops-ssh-key" in fh.read().splitlines()


def _git_ssh_command(cwd, ssh_key=None):
    script = direct_commands._git_ssh_env_prefix(ssh_key) + \
        'printf %s "$GIT_SSH_COMMAND"'
    return subprocess.run(["sh", "-c", script], cwd=cwd, capture_output=True,
                          text=True, check=True).stdout


class TestStatusAndDiffUseIt:
    def test_the_recorded_key_is_used(self, tmp_path):
        (tmp_path / ".gitops-ssh-key").write_text("/home/u/.ssh/deploy\n")
        assert _git_ssh_command(tmp_path).startswith(
            "ssh -i /home/u/.ssh/deploy ")

    def test_it_comes_before_the_kubernetes_deploy_key(self, tmp_path):
        (tmp_path / ".gitops-ssh-key").write_text("/home/u/.ssh/deploy\n")
        (tmp_path / ".gitops-deploy-key").write_text("k")
        assert "/home/u/.ssh/deploy" in _git_ssh_command(tmp_path)

    def test_an_explicit_key_wins(self, tmp_path):
        (tmp_path / ".gitops-ssh-key").write_text("/home/u/.ssh/deploy\n")
        cmd = _git_ssh_command(tmp_path, "/other")
        assert "/other" in cmd and "/home/u/.ssh/deploy" not in cmd

    def test_without_any_key_ssh_runs_plain(self, tmp_path):
        assert _git_ssh_command(tmp_path).startswith(
            "ssh -o StrictHostKeyChecking")
