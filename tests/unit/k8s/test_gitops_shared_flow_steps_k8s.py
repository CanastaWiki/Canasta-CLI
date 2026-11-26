"""The Kubernetes gitops init and join flows include the shared step files in
order and pass every parameter those steps take."""

import os

import pytest
import yaml

from _gitops_flow import GITOPS_TASKS, SHARED_FLOW_FILES, flow_tasks


@pytest.mark.parametrize("flow,steps", [
    ("join_kubernetes.yml", ["_join_require_no_repo.yml",
                             "_join_clone_repo.yml", "_join_read_hosts.yml",
                             "_join_move_git.yml", "_git_identity_repo.yml",
                             "_join_checkout_differing.yml",
                             "_register_host.yml", "_join_commit_push.yml"]),
    ("init_kubernetes.yml", ["_git_identity.yml", "_init_require_no_repo.yml",
                             "_git_identity_repo.yml",
                             "_init_convert_submodules.yml",
                             "_init_commit_push.yml"]),
])
def test_each_flow_includes_its_shared_steps_in_order(flow, steps):
    with open(os.path.join(GITOPS_TASKS, flow)) as f:
        tasks = yaml.safe_load(f)
    included = [t["ansible.builtin.include_tasks"] for t in tasks
                if t.get("ansible.builtin.include_tasks") in SHARED_FLOW_FILES]
    assert included == steps


@pytest.mark.parametrize("flow", ["join_kubernetes.yml", "init_kubernetes.yml"])
def test_every_parameter_of_a_shared_step_is_passed(flow):
    text = yaml.safe_dump(flow_tasks(flow))
    for name in ("_join_git_env", "_join_host_noun", "_join_blocked_remedy",
                 "_register_file_mode", "_register_dir_mode",
                 "_join_commit_message", "_init_git_env"):
        assert "{{ %s }}" % name not in text, (flow, name)
