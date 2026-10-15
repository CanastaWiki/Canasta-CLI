"""remove deletes the wiki's public_assets/<wiki>/ from the instance dir.

On Kubernetes the container's public_assets is a volume, so removing it
inside the container leaves the instance dir's copy. That copy is mirrored
back into the volume on start, and a gitops push's tracked-path staging
adds it back to the repo after the remove staged its deletion.
"""

import os
import subprocess
import sys

import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
REMOVE = os.path.join(REPO_ROOT, "playbooks", "remove.yml")
UNSTAGE = os.path.join(REPO_ROOT, "roles", "gitops", "tasks",
                       "unstage_removed_wiki.yml")


def _walk(tasks):
    for t in tasks or []:
        if not isinstance(t, dict):
            continue
        yield t
        for key in ("block", "rescue", "always"):
            yield from _walk(t.get(key))


def _load(path):
    with open(path) as f:
        return list(_walk(yaml.safe_load(f)))


def _git(inst, *args):
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=inst, check=True, capture_output=True, text=True).stdout.strip()


def _repo(tmp_path):
    inst = tmp_path / "inst"
    for wiki in ("main", "docs"):
        (inst / "public_assets" / wiki).mkdir(parents=True)
        (inst / "public_assets" / wiki / ".gitkeep").write_text("")
        (inst / "config" / "settings" / "wikis" / wiki).mkdir(parents=True)
        (inst / "config" / "settings" / "wikis" / wiki / "Settings.php") \
            .write_text("<?php\n")
    _git(inst, "init", "-q")
    _git(inst, "add", "-A")
    _git(inst, "commit", "-qm", "init")
    return inst


def _run(tmp_path, inst, tasks):
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {"instance_path": str(inst), "canasta_root": REPO_ROOT,
                 "wiki": "docs", "removed_wiki_id": "docs",
                 "ansible_python_interpreter": sys.executable},
        "tasks": tasks,
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0")
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    return subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env,
                          cwd=tmp_path)


def _host_file_tasks():
    """remove.yml's tasks that act on the instance dir directly."""
    return [t for t in _load(REMOVE) if "ansible.builtin.file" in t]


def _stage_deletion_task():
    return next(t for t in _load(UNSTAGE)
                if t.get("name") == "Stage the deletion of the wiki's tracked paths")


def test_remove_deletes_public_assets_dir_on_host(tmp_path):
    inst = _repo(tmp_path)
    proc = _run(tmp_path, inst, _host_file_tasks())
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not (inst / "public_assets" / "docs").exists()
    assert (inst / "public_assets" / "main" / ".gitkeep").exists()


def test_push_staging_does_not_re_add_public_assets(tmp_path):
    inst = _repo(tmp_path)
    proc = _run(tmp_path, inst,
                _host_file_tasks() + [_stage_deletion_task()])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    # gitops push stages its tracked paths with 'git add -A'.
    _git(inst, "add", "-A", "--", "public_assets", "config")
    assert _git(inst, "ls-files", "public_assets/docs",
                "config/settings/wikis/docs") == ""
    assert _git(inst, "ls-files", "public_assets/main") == \
        "public_assets/main/.gitkeep"
