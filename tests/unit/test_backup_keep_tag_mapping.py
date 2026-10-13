"""backup purge --keep-tag matches the tag as given to backup create.

`backup create -t keepme` stores `keepme__on__<hostname>`, so purge maps
the tag the operator gives the same way, and passes a tag already in that
form through unchanged.
"""

import json
import os
import socket
import subprocess
import sys

import pytest
import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
FILTER_DIR = os.path.join(REPO_ROOT, "filter_plugins")
BACKUP_TASKS = os.path.join(REPO_ROOT, "roles", "backup", "tasks")
PURGE = os.path.join(BACKUP_TASKS, "purge.yml")
CREATE = os.path.join(BACKUP_TASKS, "create.yml")

sys.path.insert(0, FILTER_DIR)
from canasta_backup import backup_stored_tag, backup_tag_match  # noqa: E402


class TestMapping:
    def test_stored_tag_names_the_host(self):
        assert backup_stored_tag("keepme", "web1") == "keepme__on__web1"

    def test_plain_tag_maps_to_this_hosts_stored_form(self):
        assert backup_tag_match("keepme", "web1") == "keepme__on__web1"

    def test_stored_form_passes_through(self):
        assert backup_tag_match("keepme__on__web2", "web1") == "keepme__on__web2"

    def test_match_agrees_with_create(self):
        for tag in ("scheduled", "before-upgrade", "a.b:c_d"):
            assert backup_tag_match(tag, "h") == backup_stored_tag(tag, "h")


def _create_tag_expr():
    def walk(tasks):
        for t in tasks:
            yield t
            yield from walk(t.get("block", []))
    with open(CREATE) as f:
        for t in walk(yaml.safe_load(f)):
            if t.get("name") == "Build tag with hostname":
                return t["ansible.builtin.set_fact"]["_backup_tag"]
    raise AssertionError("create.yml no longer builds _backup_tag")


def test_create_uses_the_shared_filter():
    assert "backup_stored_tag" in _create_tag_expr()


def _run_purge(tmp_path, extra_vars):
    """Run purge.yml with resolve_instance and run_backup stubbed out."""
    root = tmp_path / "root"
    (root / "roles" / "common" / "tasks").mkdir(parents=True)
    (root / "roles" / "orchestrator" / "tasks").mkdir(parents=True)
    (root / "roles" / "common" / "tasks" / "resolve_instance.yml").write_text(
        "- ansible.builtin.meta: noop\n")
    out = tmp_path / "args.json"
    (root / "roles" / "orchestrator" / "tasks" / "run_backup.yml").write_text(
        yaml.safe_dump([
            {"ansible.builtin.copy": {
                "content": "{{ backup_args | to_json }}", "dest": str(out)}},
            {"ansible.builtin.set_fact": {"_restic_result": {"stdout": ""}}},
        ]))
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": dict({"canasta_root": str(root)}, **extra_vars),
        "tasks": [{"ansible.builtin.include_tasks": PURGE}],
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_FILTER_PLUGINS=FILTER_DIR)
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run(
        [playbook, "-i", "localhost,", str(play)],
        capture_output=True, text=True, env=env, cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return json.loads(out.read_text())


def _hostname():
    return subprocess.run(["hostname"], capture_output=True, text=True,
                          check=True).stdout.strip() or socket.gethostname()


class TestPurgeArguments:
    def test_plain_keep_tag_is_mapped(self, tmp_path):
        args = _run_purge(tmp_path, {"keep_tag": "keepme"})
        assert args == ["forget", "--prune",
                        "--keep-tag", "keepme__on__" + _hostname()]

    def test_stored_keep_tag_is_unchanged(self, tmp_path):
        args = _run_purge(tmp_path, {"keep_tag": "keepme__on__other",
                                     "keep_last": "3"})
        assert args == ["forget", "--prune", "--keep-last", "3",
                        "--keep-tag", "keepme__on__other"]

    @pytest.mark.parametrize("extra", [{}, {"keep_tag": ""}])
    def test_no_keep_tag_adds_nothing(self, tmp_path, extra):
        args = _run_purge(tmp_path, dict({"keep_last": "5"}, **extra))
        assert args == ["forget", "--prune", "--keep-last", "5"]
