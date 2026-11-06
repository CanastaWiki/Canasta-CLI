"""The gitops .gitignore excludes the uploads directory and nothing else.

Extensions and skins ship their own images/ directories, which a gitops
repository tracking them as plain files must keep. The upgrade refresh also
replaces the unanchored rule older repositories carry.
"""

import os
import subprocess
import sys

import pytest
import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DEFAULT = os.path.join(REPO_ROOT, "roles", "gitops", "files", "gitignore.default")
REFRESH = os.path.join(REPO_ROOT, "roles", "gitops", "tasks",
                       "refresh_gitignore.yml")

UPLOAD = "images/a/ab/Upload.png"
SHIPPED = ["extensions/PageForms/images/DatePickerButton.gif",
           "skins/MySkin/images/logo.png"]


def _instance(tmp_path, gitignore):
    inst = tmp_path / "inst"
    inst.mkdir()
    subprocess.run(["git", "init", "-q", str(inst)], check=True)
    (inst / ".gitignore").write_text(gitignore)
    (inst / ".gitops-host").write_text("hosta\n")
    for path in [UPLOAD] + SHIPPED:
        (inst / path).parent.mkdir(parents=True, exist_ok=True)
        (inst / path).write_text("x")
    return inst


def _ignored(inst, path):
    return subprocess.run(["git", "-C", str(inst), "check-ignore", "-q", path],
                          check=False).returncode == 0


def _refresh(tmp_path, inst, k8s):
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {"instance_path": str(inst), "canasta_root": REPO_ROOT,
                 "instance_is_k8s": k8s,
                 "ansible_python_interpreter": sys.executable},
        "tasks": [{"ansible.builtin.include_tasks": REFRESH}],
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_LOCAL_TEMP=str(tmp_path / "ansible-tmp"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env, cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_default_ignores_uploads_but_not_shipped_images(tmp_path):
    with open(DEFAULT) as f:
        inst = _instance(tmp_path, f.read())
    assert _ignored(inst, UPLOAD)
    for path in SHIPPED:
        assert not _ignored(inst, path), path


@pytest.mark.parametrize("k8s", [False, True])
def test_refresh_replaces_the_unanchored_rule(tmp_path, k8s):
    inst = _instance(tmp_path, ".env\nimages/\nmy-own-rule\n")
    _refresh(tmp_path, inst, k8s)
    lines = (inst / ".gitignore").read_text().splitlines()
    assert "images/" not in lines
    assert lines.count("/images/") == 1
    assert "my-own-rule" in lines
    assert _ignored(inst, UPLOAD)
    for path in SHIPPED:
        assert not _ignored(inst, path), path
    before = (inst / ".gitignore").read_text()
    _refresh(tmp_path, inst, k8s)
    assert (inst / ".gitignore").read_text() == before
