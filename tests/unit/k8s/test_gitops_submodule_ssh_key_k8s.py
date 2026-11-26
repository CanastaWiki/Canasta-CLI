"""Kubernetes join checks out submodules with the clone's SSH command.

join_kubernetes.yml passes a GIT_SSH_COMMAND built from the staged deploy
key (or --ssh-key) to every submodule update it includes.
"""

from test_gitops_submodule_ssh_key import _tasks


def test_kubernetes_join_passes_the_clone_ssh_command_to_submodule_updates():
    includes = [t for t in _tasks("join_kubernetes.yml")
                if "_update_submodules.yml"
                in str(t.get("ansible.builtin.include_tasks", ""))]
    assert includes
    for t in includes:
        env = (t.get("vars") or {}).get("submodule_git_env") or {}
        assert "default(_ssh_key_target_path, true)" in env.get(
            "GIT_SSH_COMMAND", ""), t.get("name")
