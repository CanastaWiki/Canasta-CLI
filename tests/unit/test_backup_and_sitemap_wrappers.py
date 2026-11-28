"""The backup and sitemap commands that hand one call to the orchestrator.

Each task file runs against stub resolve_instance, run_backup and exec tasks
that log what they were given, so these check what each command asks restic
or the web container to do, and that it shows the result.
"""

import json
import os
import subprocess
import sys

import pytest
import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
LIBRARY = os.path.join(REPO_ROOT, "roles", "common", "library")
MODULE_UTILS = os.path.join(REPO_ROOT, "roles", "common", "module_utils")
RESTIC_OUTPUT = "restic output for the test"


def _stub_root(tmp_path, log):
    root = tmp_path / "root"
    stubs = {
        "roles/common/tasks/resolve_instance.yml": [
            {"ansible.builtin.set_fact": {
                "instance_path": str(tmp_path / "instance")}}],
        "roles/orchestrator/tasks/run_backup.yml": [
            {"ansible.builtin.lineinfile": {
                "path": str(log), "create": True, "mode": "0600",
                "line": "{{ backup_args | to_json }}"}},
            {"ansible.builtin.set_fact": {
                "_restic_result": {"stdout": RESTIC_OUTPUT}}}],
        "roles/orchestrator/tasks/exec.yml": [
            {"ansible.builtin.lineinfile": {
                "path": str(log), "create": True, "mode": "0600",
                "line": "{{ [exec_service, exec_command] | to_json }}"}}],
    }
    for rel, tasks in stubs.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(tasks))
    return root


def _run(tmp_path, tasks_file, **variables):
    log = tmp_path / "calls.log"
    (tmp_path / "instance" / "config").mkdir(parents=True)
    (tmp_path / "instance" / "config" / "wikis.yaml").write_text(
        yaml.safe_dump({"wikis": [{"id": "main", "url": "example.com"},
                                  {"id": "docs", "url": "example.com/docs"}]}))
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": dict(variables, canasta_root=str(_stub_root(tmp_path, log)),
                     ansible_python_interpreter=sys.executable),
        "tasks": [{"ansible.builtin.include_tasks":
                   os.path.join(REPO_ROOT, tasks_file)}],
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_LIBRARY=LIBRARY, ANSIBLE_MODULE_UTILS=MODULE_UTILS)
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env, cwd=tmp_path)
    calls = ([json.loads(line) for line in log.read_text().splitlines()]
             if log.exists() else [])
    return proc, calls


@pytest.mark.parametrize("tasks_file, variables, restic_args", [
    ("roles/backup/tasks/delete.yml", {"snapshot": "abc123"},
     ["forget", "abc123", "--prune"]),
    ("roles/backup/tasks/unlock.yml", {}, ["unlock"]),
    ("roles/backup/tasks/files.yml", {"snapshot": "abc123"},
     ["ls", "abc123"]),
    ("roles/backup/tasks/check.yml", {}, ["check"]),
    ("roles/backup/tasks/diff.yml", {"snapshot": "abc123", "snapshot2": "def456"},
     ["diff", "abc123", "def456"]),
])
def test_backup_command_runs_restic_and_shows_its_output(
        tmp_path, tasks_file, variables, restic_args):
    proc, calls = _run(tmp_path, tasks_file, **variables)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert calls == [restic_args]
    assert RESTIC_OUTPUT in out


def test_sitemap_remove_clears_every_wiki_by_default(tmp_path):
    proc, calls = _run(tmp_path, "roles/sitemap/tasks/remove.yml")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert calls == [
        ["web", "rm -rf /mediawiki/public_assets/main/sitemap"],
        ["web", "rm -rf /mediawiki/public_assets/docs/sitemap"],
    ]
    assert "Sitemaps removed for: main, docs" in out


def test_sitemap_remove_clears_only_the_named_wiki(tmp_path):
    proc, calls = _run(tmp_path, "roles/sitemap/tasks/remove.yml", wiki="docs")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert calls == [["web", "rm -rf /mediawiki/public_assets/docs/sitemap"]]


def test_sitemap_remove_refuses_a_wiki_the_instance_lacks(tmp_path):
    proc, calls = _run(tmp_path, "roles/sitemap/tasks/remove.yml", wiki="nope")
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0
    assert calls == []
    assert "Wiki 'nope' is not in this instance. Present wikis: main, docs." in out
