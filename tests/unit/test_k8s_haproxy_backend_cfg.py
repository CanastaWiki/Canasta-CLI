"""Kubernetes instances carry no haproxy-backend.cfg.

The file serves the HAProxy edge, which fronts Compose instances. Earlier
releases wrote it into Kubernetes instances too, where it showed as untracked
in gitops; the upgrade removes an untouched, untracked copy.
"""
import os
import subprocess
import sys

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TASKS = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks",
                     "remove_k8s_haproxy_backend_cfg.yml")
INIT = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks", "init_config.yml")
TEMPLATE = os.path.join(REPO_ROOT, "instance_template", "config", "haproxy-backend.cfg")
CFG = "config/haproxy-backend.cfg"


def _git(inst, *args):
    subprocess.run(["git", *args], cwd=inst, capture_output=True, text=True, check=True)


def _instance(tmp_path, content=None, git=False, track=False):
    inst = tmp_path / "inst"
    (inst / "config").mkdir(parents=True)
    if content is None:
        with open(TEMPLATE) as fh:
            content = fh.read()
    (inst / CFG).write_text(content)
    if git:
        _git(inst, "init", "-q")
        if track:
            _git(inst, "add", CFG)
            _git(inst, "-c", "user.name=t", "-c", "user.email=t@example.com",
                 "-c", "commit.gpgsign=false", "commit", "-q", "-m", "init")
    return inst


def _run(tmp_path, inst):
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost", "connection": "local", "gather_facts": False,
        "vars": {"instance_path": str(inst), "canasta_root": REPO_ROOT,
                 "ansible_python_interpreter": sys.executable},
        "tasks": [{"ansible.builtin.include_tasks": TASKS}],
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_CONFIG=os.path.join(REPO_ROOT, "ansible.cfg"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env, cwd=REPO_ROOT)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_an_untouched_untracked_copy_is_removed(tmp_path):
    inst = _instance(tmp_path, git=True)
    _run(tmp_path, inst)
    assert not (inst / CFG).exists()


def test_an_untouched_copy_outside_gitops_is_removed(tmp_path):
    inst = _instance(tmp_path)
    _run(tmp_path, inst)
    assert not (inst / CFG).exists()


def test_an_edited_copy_is_kept(tmp_path):
    inst = _instance(tmp_path, content="timeout server 300s\n", git=True)
    _run(tmp_path, inst)
    assert (inst / CFG).exists()


def test_a_tracked_copy_is_kept(tmp_path):
    inst = _instance(tmp_path, git=True, track=True)
    _run(tmp_path, inst)
    assert (inst / CFG).exists()


def test_a_missing_file_is_not_an_error(tmp_path):
    inst = tmp_path / "inst"
    inst.mkdir()
    _run(tmp_path, inst)


def test_init_config_runs_it_on_kubernetes_only():
    with open(INIT) as fh:
        top = yaml.safe_load(fh)
    task = next(t for t in top
                if t.get("name") == "Remove an unused haproxy-backend.cfg from a Kubernetes instance")
    assert task["ansible.builtin.include_tasks"] == "remove_k8s_haproxy_backend_cfg.yml"
    assert task["when"] == "instance_is_k8s | bool"
    assert not any(t.get("name") == "Create haproxy-backend.cfg from template (no-clobber)"
                   for t in top), "the file must not be created outside the Compose block"
