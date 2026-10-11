"""Kubernetes gitops guards: push role, diff fetch, and the cluster's sops-age key.

Each test runs the relevant tasks in a localhost play. Tasks that need a
live cluster (k8s_info) are left out and their registered results are
supplied as play vars instead.
"""

import base64
import os
import shutil
import subprocess
import sys

import pytest
import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TASKS = os.path.join(REPO_ROOT, "roles", "gitops", "tasks")
COMMON = os.path.join(REPO_ROOT, "roles", "common")

needs_age = pytest.mark.skipif(
    shutil.which("age-keygen") is None, reason="age-keygen not installed")


def _load(name):
    with open(os.path.join(TASKS, name)) as f:
        return yaml.safe_load(f)


def _play(tmp_path, tasks, extra_vars, env_extra=None):
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": dict({"canasta_root": REPO_ROOT,
                      "ansible_python_interpreter": sys.executable},
                     **extra_vars),
        "tasks": tasks,
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_FILTER_PLUGINS=os.path.join(REPO_ROOT, "filter_plugins"),
               ANSIBLE_LIBRARY=os.path.join(COMMON, "library"),
               ANSIBLE_MODULE_UTILS=os.path.join(COMMON, "module_utils"),
               **(env_extra or {}))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env,
                          cwd=tmp_path)
    return proc.returncode, proc.stdout + proc.stderr


def _git(cwd, *args):
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com",
         "-c", "commit.gpgsign=false", *args],
        cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


# --- gitops push refuses hosts whose role cannot push ---

def _push_guard_tasks():
    tasks = _load("push_kubernetes.yml")
    names = [t.get("name") for t in tasks]
    return tasks[:names.index("Fail if role does not allow push") + 1]


def _push_instance(tmp_path, role):
    inst = tmp_path / "inst"
    (inst / "hosts").mkdir(parents=True)
    entry = {"name": "prod"}
    if role:
        entry["role"] = role
    (inst / "hosts" / "hosts.yaml").write_text(
        yaml.safe_dump({"hosts": [entry]}))
    (inst / ".gitops-host").write_text("prod\n")
    return inst


@pytest.mark.parametrize("role", ["sink", "receive"])
def test_k8s_push_refuses_non_source_role(tmp_path, role):
    inst = _push_instance(tmp_path, role)
    rc, out = _play(tmp_path, _push_guard_tasks(),
                    {"instance_path": str(inst), "instance_id": "t"})
    assert rc != 0
    assert "does not allow pushing" in out


@pytest.mark.parametrize("role", ["source", "both", None])
def test_k8s_push_allows_source_roles(tmp_path, role):
    inst = _push_instance(tmp_path, role)
    rc, out = _play(tmp_path, _push_guard_tasks(),
                    {"instance_path": str(inst), "instance_id": "t"})
    assert rc == 0, out


# --- gitops diff fetches before comparing with the remote ---

def _diff_repo(tmp_path, reachable):
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    inst = tmp_path / "inst"
    inst.mkdir()
    _git(inst, "init", "-q", "-b", "main")
    (inst / "a.txt").write_text("a\n")
    _git(inst, "add", "a.txt")
    _git(inst, "commit", "-qm", "one")
    _git(inst, "remote", "add", "origin", str(remote))
    _git(inst, "push", "-q", "-u", "origin", "main")
    other = tmp_path / "other"
    _git(tmp_path, "clone", "-q", str(remote), str(other))
    (other / "b.txt").write_text("b\n")
    _git(other, "add", "b.txt")
    _git(other, "commit", "-qm", "two")
    _git(other, "push", "-q")
    if not reachable:
        _git(inst, "remote", "set-url", "origin", str(tmp_path / "missing.git"))
    return inst


def _run_diff(tmp_path, inst):
    return _play(tmp_path, _load("diff_kubernetes.yml"), {
        "instance_path": str(inst),
        "instance_id": "unit-diff-%d" % os.getpid(),
        "stat": True,
        "gitops_k8s_git_env": {"GIT_SSH_COMMAND": "ssh"},
    })


def test_k8s_diff_fetches_remote_changes(tmp_path):
    rc, out = _run_diff(tmp_path, _diff_repo(tmp_path, reachable=True))
    assert rc == 0, out
    assert "Remote changes (would be applied on pull): 1 file(s)" in out
    assert "could not fetch" not in out


def test_k8s_diff_notes_a_failed_fetch(tmp_path):
    rc, out = _run_diff(tmp_path, _diff_repo(tmp_path, reachable=False))
    assert rc == 0, out
    assert "Note: could not fetch from the remote" in out
    assert "Remote changes (would be applied on pull): 0 file(s)" in out


# --- the controller's operator key must match the cluster's sops-age Secret ---

def _keygen(path):
    subprocess.run(["age-keygen", "-o", str(path)], check=True,
                   capture_output=True)
    return path.read_text()


def _recipient(path):
    return subprocess.run(["age-keygen", "-y", str(path)], check=True,
                          capture_output=True, text=True).stdout.strip()


def _cluster_secret(key_text):
    if key_text is None:
        return {"resources": []}
    return {"resources": [{"data": {
        "keys.txt": base64.b64encode(key_text.encode()).decode()}}]}


def _init_sops_key_tasks():
    tasks = _load("_init_sops.yml")
    assert tasks[1]["kubernetes.core.k8s_info"]["name"] == "sops-age"
    assert "block" in tasks[2]
    return [tasks[0], tasks[2]]


def _run_init_key(tmp_path, cluster_key):
    cfg = tmp_path / "cfg"
    return _play(tmp_path, _init_sops_key_tasks(), {
        "host_name": "staging",
        "_sops_age_secret": _cluster_secret(cluster_key),
    }, {"CANASTA_CONFIG_DIR": str(cfg)}), cfg / "sops" / "staging.age"


@needs_age
def test_init_generates_key_on_a_cluster_without_sops_age(tmp_path):
    (rc, out), key = _run_init_key(tmp_path, None)
    assert rc == 0, out
    assert key.exists()


@needs_age
def test_init_refuses_to_mint_a_second_key_for_the_cluster(tmp_path):
    cluster = _keygen(tmp_path / "prod.age")
    (rc, out), key = _run_init_key(tmp_path, cluster)
    assert rc != 0
    assert "already holds an operator age key" in out
    assert not key.exists()


@needs_age
def test_init_refuses_a_controller_key_that_differs_from_the_cluster(tmp_path):
    cluster = _keygen(tmp_path / "prod.age")
    key = tmp_path / "cfg" / "sops" / "staging.age"
    key.parent.mkdir(parents=True)
    _keygen(key)
    (rc, out), _ = _run_init_key(tmp_path, cluster)
    assert rc != 0
    assert "differs from the key in this" in out


@needs_age
def test_init_accepts_the_cluster_key(tmp_path):
    cluster = _keygen(tmp_path / "prod.age")
    key = tmp_path / "cfg" / "sops" / "staging.age"
    key.parent.mkdir(parents=True)
    key.write_text(cluster)
    (rc, out), _ = _run_init_key(tmp_path, cluster)
    assert rc == 0, out


def _run_join_key(tmp_path, repo_key, cluster_key):
    tasks = _load("_join_sops.yml")
    names = [t.get("name") for t in tasks]
    start = names.index("Save connection state before controller-side SOPS key read")
    end = names.index("Fail if the cluster already decrypts with a different operator key")
    return _play(tmp_path, tasks[start:end + 1], {
        "key": str(repo_key.with_suffix("")),
        "_join_repo_recipient": _recipient(repo_key),
        "_js_sops_secret": _cluster_secret(cluster_key),
    })


@needs_age
def test_join_refuses_a_cluster_holding_another_key(tmp_path):
    repo_key = tmp_path / "repo.age"
    _keygen(repo_key)
    other = _keygen(tmp_path / "other.age")
    rc, out = _run_join_key(tmp_path, repo_key, other)
    assert rc != 0
    assert "holds a different operator age key" in out


@needs_age
@pytest.mark.parametrize("same", [True, False])
def test_join_accepts_a_matching_or_absent_cluster_key(tmp_path, same):
    repo_key = tmp_path / "repo.age"
    text = _keygen(repo_key)
    rc, out = _run_join_key(tmp_path, repo_key, text if same else None)
    assert rc == 0, out


# --- delete removes the instance's Argo CD repository Secret ---

def test_delete_removes_the_argo_repo_secret():
    path = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks",
                        "delete_remove_argocd_app.yml")
    with open(path) as f:
        tasks = yaml.safe_load(f)
    secrets = [t for t in tasks
               if t.get("kubernetes.core.k8s", {}).get("kind") == "Secret"]
    assert len(secrets) == 1
    spec = secrets[0]["kubernetes.core.k8s"]
    assert spec["state"] == "absent"
    assert spec["name"] == "canasta-{{ instance_id }}-repo"
    assert spec["namespace"] == "{{ argocd_namespace | default('argocd') }}"
    assert secrets[0].get("ignore_errors") is True
