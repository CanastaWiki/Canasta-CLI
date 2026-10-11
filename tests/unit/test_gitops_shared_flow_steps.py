"""The steps gitops init and join share, run on a local git checkout.

Compose and Kubernetes include the same files for these steps and pass in
what differs (the git environment, the host noun, file modes, the commit
identity). Each case runs a step as a playbook on localhost.
"""

import os
import stat
import subprocess
import sys

import pytest
import yaml

from _gitops_flow import GITOPS_TASKS, SHARED_FLOW_FILES, flow_tasks

ENV = {"GIT_SSH_COMMAND": "ssh"}


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout


def _run(tmp_path, step, play_vars, include_vars=None):
    include = {"ansible.builtin.include_tasks": os.path.join(GITOPS_TASKS, step)}
    if include_vars:
        include["vars"] = include_vars
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": dict(play_vars, ansible_python_interpreter=sys.executable),
        "tasks": [include],
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_LOCAL_TEMP=str(tmp_path / "ansible-tmp"),
               HOME=str(tmp_path))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    return subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env, cwd=tmp_path)


@pytest.mark.parametrize("flow,steps", [
    ("join.yml", ["_git_identity.yml", "_join_require_no_repo.yml",
                  "_join_clone_repo.yml", "_join_read_hosts.yml",
                  "_join_move_git.yml", "_join_checkout_differing.yml",
                  "_register_host.yml", "_join_commit_push.yml"]),
    ("join_kubernetes.yml", ["_join_require_no_repo.yml",
                             "_join_clone_repo.yml", "_join_read_hosts.yml",
                             "_join_move_git.yml",
                             "_join_checkout_differing.yml",
                             "_register_host.yml", "_join_commit_push.yml"]),
    ("init_compose.yml", ["_git_identity.yml", "_init_require_no_repo.yml",
                          "_init_convert_submodules.yml",
                          "_init_commit_push.yml"]),
    ("init_kubernetes.yml", ["_git_identity.yml", "_init_require_no_repo.yml",
                             "_init_convert_submodules.yml",
                             "_init_commit_push.yml"]),
])
def test_each_flow_includes_its_shared_steps_in_order(flow, steps):
    with open(os.path.join(GITOPS_TASKS, flow)) as f:
        tasks = yaml.safe_load(f)
    included = [t["ansible.builtin.include_tasks"] for t in tasks
                if t.get("ansible.builtin.include_tasks") in SHARED_FLOW_FILES]
    assert included == steps


@pytest.mark.parametrize("flow", ["join.yml", "join_kubernetes.yml",
                                  "init_compose.yml", "init_kubernetes.yml"])
def test_every_parameter_of_a_shared_step_is_passed(flow):
    text = yaml.safe_dump(flow_tasks(flow))
    for name in ("_join_git_env", "_join_host_noun", "_join_blocked_remedy",
                 "_register_file_mode", "_register_dir_mode",
                 "_join_commit_message", "_init_git_env"):
        assert "{{ %s }}" % name not in text, (flow, name)


@pytest.mark.parametrize("noun,message", [
    ("host", "Host 'h1' already exists in the gitops repository. Choose a "
             "different name, remove the existing host first, or pass "
             "--reinit to re-attach this host."),
    ("environment", "Environment 'h1' already exists in the gitops "
                    "repository. Choose a different name, remove the "
                    "existing environment first, or pass --reinit to "
                    "re-attach this environment."),
])
def test_read_hosts_refuses_a_registered_name(tmp_path, noun, message):
    (tmp_path / "clone" / "hosts").mkdir(parents=True)
    (tmp_path / "clone" / "hosts" / "hosts.yaml").write_text(
        "hosts:\n  - name: h1\n    role: both\n")
    play_vars = {"host_name": "h1",
                 "_join_tmpdir": {"path": str(tmp_path / "clone")}}
    proc = _run(tmp_path, "_join_read_hosts.yml", play_vars,
                {"_join_host_noun": noun})
    assert proc.returncode != 0
    assert message in proc.stdout + proc.stderr
    proc = _run(tmp_path, "_join_read_hosts.yml", dict(play_vars, reinit=True),
                {"_join_host_noun": noun})
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_register_host_upserts_with_the_given_modes(tmp_path):
    inst = tmp_path / "inst"
    (inst / "hosts").mkdir(parents=True)
    proc = _run(tmp_path, "_register_host.yml", {
        "instance_path": str(inst), "host_name": "h2", "role": "sink",
        "_join_hosts": {"hosts": [{"name": "h1", "role": "both"},
                                  {"name": "h2", "role": "both"}]},
    }, {"_register_file_mode": "0600", "_register_dir_mode": "0700"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    hosts = yaml.safe_load((inst / "hosts" / "hosts.yaml").read_text())
    assert hosts == {"hosts": [{"name": "h1", "role": "both"},
                               {"name": "h2", "role": "sink"}]}
    assert stat.S_IMODE(os.stat(inst / "hosts" / "hosts.yaml").st_mode) == 0o600
    assert stat.S_IMODE(os.stat(inst / "hosts" / "h2").st_mode) == 0o700


def _repo_with_remote(tmp_path):
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    inst = tmp_path / "inst"
    inst.mkdir()
    _git(inst, "init", "-q", "-b", "main")
    _git(inst, "config", "user.name", "t")
    _git(inst, "config", "user.email", "t@example.com")
    (inst / "a.txt").write_text("a\n")
    (inst / "b.txt").write_text("b\n")
    _git(inst, "add", "-A")
    _git(inst, "commit", "-q", "-m", "init")
    _git(inst, "remote", "add", "origin", str(remote))
    _git(inst, "push", "-q", "-u", "origin", "main")
    return inst, remote


def test_checkout_rewrites_only_the_differing_files(tmp_path):
    inst, _ = _repo_with_remote(tmp_path)
    (inst / "a.txt").write_text("changed\n")
    b_inode = os.stat(inst / "b.txt").st_ino
    proc = _run(tmp_path, "_join_checkout_differing.yml",
                {"instance_path": str(inst)},
                {"_join_blocked_remedy": "Fix it."})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (inst / "a.txt").read_text() == "a\n"
    assert os.stat(inst / "b.txt").st_ino == b_inode
    assert _git(inst, "config", "pull.rebase").strip() == "true"


@pytest.mark.skipif(os.geteuid() == 0, reason="root can replace any file")
def test_checkout_failure_names_the_files_and_the_remedy(tmp_path):
    inst, _ = _repo_with_remote(tmp_path)
    (inst / "locked").mkdir()
    _git(inst, "mv", "a.txt", "locked/a.txt")
    _git(inst, "commit", "-q", "-m", "move")
    (inst / "locked" / "a.txt").write_text("changed\n")
    os.chmod(inst / "locked", 0o555)
    try:
        proc = _run(tmp_path, "_join_checkout_differing.yml",
                    {"instance_path": str(inst), "id": "x"},
                    {"_join_blocked_remedy": "Run canasta stop -i {{ id }}."})
    finally:
        os.chmod(inst / "locked", 0o755)
    assert proc.returncode != 0
    out = proc.stdout + proc.stderr
    assert "locked/a.txt" in out
    assert "Run canasta stop -i x." in out


def test_commit_push_skips_the_commit_when_nothing_is_staged(tmp_path):
    inst, remote = _repo_with_remote(tmp_path)
    head = _git(inst, "rev-parse", "HEAD")
    proc = _run(tmp_path, "_join_commit_push.yml",
                {"instance_path": str(inst), "host_name": "h1"},
                {"_join_commit_message": "Add host {{ host_name }}",
                 "_join_git_env": ENV})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _git(inst, "rev-parse", "HEAD") == head


def test_commit_push_commits_with_the_message_and_identity(tmp_path):
    inst, remote = _repo_with_remote(tmp_path)
    (inst / "c.txt").write_text("c\n")
    _git(inst, "add", "c.txt")
    proc = _run(tmp_path, "_join_commit_push.yml",
                {"instance_path": str(inst), "host_name": "h1"},
                {"_join_commit_message": "Add environment {{ host_name }}",
                 "_join_commit_identity":
                     "-c user.name={{ 'O Brien' | quote }} "
                     "-c user.email=o@example.com",
                 "_join_git_env": ENV})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    log = _git(remote, "log", "-1", "--format=%s|%an|%ae", "main").strip()
    assert log == "Add environment h1|O Brien|o@example.com"


def test_init_commit_push_sets_up_the_remote(tmp_path):
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    inst = tmp_path / "inst"
    inst.mkdir()
    _git(inst, "init", "-q", "-b", "main")
    _git(inst, "config", "user.name", "t")
    _git(inst, "config", "user.email", "t@example.com")
    (inst / "a.txt").write_text("a\n")
    _git(inst, "add", "a.txt")
    proc = _run(tmp_path, "_init_commit_push.yml",
                {"instance_path": str(inst), "repo": str(remote)},
                {"_init_git_env": ENV})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _git(remote, "log", "-1", "--format=%s", "main").strip() == \
        "Initial gitops configuration"
    assert _git(inst, "rev-parse", "--abbrev-ref", "main@{upstream}").strip() \
        == "origin/main"
