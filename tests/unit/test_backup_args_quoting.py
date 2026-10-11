"""restic arguments reach a shell, so each one must be quoted, and the
backup tag is held to restic's single-tag character set at the CLI."""

import os
import shlex
import sys

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, REPO_ROOT)

import canasta  # noqa: E402

ORCH = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks")
ARGS = ["--host", "mysite", "--tag", "before upgrade;touch /x", "backup",
        "/currentsnapshot"]


def _walk(tasks):
    for task in tasks:
        yield task
        for key in ("block", "rescue", "always"):
            yield from _walk(task.get(key, []))


def _task(path, name):
    with open(path) as f:
        return next(t for t in _walk(yaml.safe_load(f))
                    if t.get("name") == name)


def _render(template, **variables):
    templar = Templar(loader=DataLoader(), variables=variables)
    return templar.template(trust_as_template(template))


def test_compose_restic_args_stay_whole():
    task = _task(os.path.join(ORCH, "run_backup.yml"), "Run restic container")
    cmd = _render(
        task["ansible.builtin.shell"]["cmd"], backup_args=ARGS,
        inspect_command="docker", instance_path="/srv/mysite",
        _backup_sources={}, _bvol="vol", _backup_local_repo="",
        _backup_tee="")
    run_line = cmd.replace("\\\n", " ").splitlines()[-1]
    words = shlex.split(run_line)
    assert words[-len(ARGS):] == ARGS


def test_k8s_restic_args_stay_whole():
    task = _task(os.path.join(ORCH, "k8s_run_backup.yml"),
                 "Build the restic container command")
    cmd = _render(task["ansible.builtin.set_fact"]["_restic_command"],
                  backup_args=ARGS + ["--keep-last", 3])
    assert shlex.split(cmd) == [
        "restic", "--cache-dir", "/tmp/restic-cache"] + ARGS + [
        "--keep-last", "3"]


def _tag_errors(value):
    cmd_def = next(c for c in canasta.load_definitions()["commands"]
                   if c["name"] == "backup_create")
    return canasta._validate_named_validators(
        cmd_def, lambda name: value if name == "tag" else None)


def test_backup_tag_accepts_plain_tags():
    for tag in ("before-upgrade", "scheduled", "v1.2_rc:3"):
        assert _tag_errors(tag) == []


def test_backup_tag_rejects_spaces_commas_and_shell():
    for tag in ("before upgrade", "a,b", "x;rm", "$(id)"):
        errors = _tag_errors(tag)
        assert errors and errors[0][0] == 1
        assert "not a valid backup tag" in errors[0][1]
