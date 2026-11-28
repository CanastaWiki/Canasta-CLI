"""A snapshot an older major release took is checked before the restore
changes anything, and a full restore of one is brought up to this release
the way an upgrade would be, instead of running the old image."""

import os
import stat
import subprocess
import sys

import pytest
import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "roles", "common", "module_utils"))
sys.path.insert(0, os.path.join(REPO_ROOT, "filter_plugins"))

from canasta_image_pin import predates_release_major
from canasta_images import FilterModule

TASKS = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks")
RESTORE_INSTANCE = os.path.join(TASKS, "restore_instance.yml")
CHECK = os.path.join(TASKS, "restore_older_release_check.yml")
REFRESH = os.path.join(TASKS, "restore_older_release_refresh.yml")
UPGRADE_MAIN = os.path.join(REPO_ROOT, "roles", "upgrade", "tasks", "main.yml")
MIGRATIONS = os.path.join(REPO_ROOT, "roles", "upgrade", "tasks", "run_migrations.yml")

NEW = "ghcr.io/canastawiki/canasta:5.0.0"


def _walk(tasks):
    for task in tasks or []:
        yield task
        for key in ("block", "rescue", "always"):
            yield from _walk(task.get(key))


def _tasks(path):
    with open(path) as fh:
        return list(_walk(yaml.safe_load(fh)))


def _names(path):
    return [t.get("name") for t in _tasks(path)]


@pytest.mark.parametrize("value, target, older", [
    ("ghcr.io/canastawiki/canasta:3.6.2", NEW, True),
    ("'ghcr.io/canastawiki/canasta:3.6.2'", NEW, True),
    ("ghcr.io/canastawiki/canasta-base:3.6.2", NEW, True),
    ("ghcr.io/canastawiki/canasta:5.0.0", "ghcr.io/canastawiki/canasta:5.1.0", False),
    ("ghcr.io/canastawiki/canasta:5.1.0", "ghcr.io/canastawiki/canasta:5.0.0", False),
    ("ghcr.io/canastawiki/canasta:latest", NEW, False),
    ("canasta:local", NEW, False),
    ("example.com/mine/canasta:3.6.2", NEW, False),
    ("", NEW, False),
    ("ghcr.io/canastawiki/canasta:3.6.2", "canasta:local", False),
])
def test_only_a_release_pin_of_an_older_major_predates(value, target, older):
    assert predates_release_major(value, target) is older


def test_the_filter_is_registered():
    assert FilterModule().filters()["canasta_predates_release_major"] is predates_release_major


def test_upgrade_and_restore_share_one_migration_list():
    upgrade = next(t for t in _tasks(UPGRADE_MAIN) if t.get("name") == "Run migrations")
    assert upgrade["ansible.builtin.include_tasks"] == "run_migrations.yml"
    refresh = next(t for t in _tasks(REFRESH)
                   if t.get("name") == "Run the upgrade's migrations on the restored files")
    assert refresh["ansible.builtin.include_tasks"].strip().endswith(
        "/roles/upgrade/tasks/run_migrations.yml")
    loop = next(t for t in _tasks(MIGRATIONS) if t.get("name") == "Run migrations")["loop"]
    assert "elasticsearch_to_opensearch.yml" in loop


def test_the_check_runs_before_the_restore_changes_anything():
    names = _names(RESTORE_INSTANCE)
    assert names.index("Check a snapshot an older major release took") \
        < names.index("Mark the files phase")


def test_the_refresh_runs_on_the_restored_files_before_the_import():
    names = _names(RESTORE_INSTANCE)
    i = names.index("Bring an older major release's files up to this release")
    assert names.index("Preserve MediaWiki's database account in restored .env") < i
    assert i < names.index("Read wiki IDs for DB restore")
    task = _tasks(RESTORE_INSTANCE)[i]
    assert task["when"] == ["wiki is not defined", "_restore_older_release | bool"]


def test_the_refresh_moves_the_pin_and_rewrites_the_stack():
    tasks = {t.get("name"): t for t in _tasks(REFRESH)}
    pin = tasks["Move the restored image pin to this release"]
    assert pin["ansible.builtin.include_tasks"] == "upgrade_image_tag.yml"
    assert pin["vars"]["_upgrade_rebuild"] is False
    stack = tasks["Refresh the stack files"]
    assert stack["ansible.builtin.include_role"]["tasks_from"] == "write_stack_files.yml"
    assert stack["vars"]["stack_files_force"] is True


# --- The check, run for real against a stand-in container runtime ---

FAKE_RUNTIME = """#!/bin/sh
case "$*" in
  *--entrypoint\\ python3*) exec "{python}" - "{snapshot}" '[]' ;;
  *) sed -n 's/^CANASTA_IMAGE=//p' "{snapshot}/.env" ;;
esac
"""


def _write(base, rel, text):
    path = base / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _run_check(tmp_path, snapshot, wiki=None):
    runtime = tmp_path / "runtime"
    runtime.write_text(FAKE_RUNTIME.format(python=sys.executable, snapshot=snapshot))
    runtime.chmod(runtime.stat().st_mode | stat.S_IXUSR)
    play_vars = {
        "canasta_root": REPO_ROOT,
        "canasta_default_image": NEW,
        "inspect_command": str(runtime),
        "instance_path": str(tmp_path / "inst"),
        "instance_id": "inst",
        "_resolved_snapshot": "abc123",
        "_restore_wiki_account": {"variables": {"CANASTA_IMAGE": NEW}},
        "ansible_python_interpreter": sys.executable,
    }
    if wiki:
        play_vars["wiki"] = wiki
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost", "connection": "local", "gather_facts": False,
        "vars": play_vars,
        "tasks": [
            {"ansible.builtin.include_tasks": CHECK},
            {"ansible.builtin.debug": {"msg": "older={{ _restore_older_release }}"}},
        ],
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_FILTER_PLUGINS=os.path.join(REPO_ROOT, "filter_plugins"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    return subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env, cwd=tmp_path)


def _snapshot(tmp_path, image, global_exts=(), wiki_exts=None):
    snap = tmp_path / "snap"
    _write(snap, ".env", "CANASTA_IMAGE=%s\n" % image)
    _write(snap, "config/settings/global/settings.yaml",
           yaml.safe_dump({"extensions": list(global_exts)}))
    for wiki, exts in (wiki_exts or {}).items():
        _write(snap, "config/settings/wikis/%s/settings.yaml" % wiki,
               yaml.safe_dump({"extensions": exts}))
    return snap


def test_a_clean_older_snapshot_passes(tmp_path):
    snap = _snapshot(tmp_path, "ghcr.io/canastawiki/canasta:3.6.2", ["Cite"])
    proc = _run_check(tmp_path, snap)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "older=True" in proc.stdout


def test_an_older_snapshot_this_release_cannot_run_is_refused(tmp_path):
    snap = _snapshot(tmp_path, "ghcr.io/canastawiki/canasta:3.6.2", ["Lingo"])
    proc = _run_check(tmp_path, snap)
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, out
    assert "Snapshot abc123 was taken on ghcr.io/canastawiki/canasta:3.6.2" in out
    assert "Lingo is enabled for all wikis" in out


def test_a_snapshot_of_this_major_is_not_probed(tmp_path):
    snap = _snapshot(tmp_path, NEW, ["Lingo"])
    proc = _run_check(tmp_path, snap)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "older=False" in proc.stdout


def test_a_single_wiki_restore_only_weighs_that_wikis_findings(tmp_path):
    snap = _snapshot(tmp_path, "ghcr.io/canastawiki/canasta:3.6.2", ["Lingo"],
                     {"main": ["Cite"], "docs": ["Interwiki"]})
    proc = _run_check(tmp_path, snap, wiki="main")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    proc = _run_check(tmp_path, snap, wiki="docs")
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, out
    assert "Interwiki is enabled for wiki 'docs'" in out
    assert "Lingo" not in out.split("cannot run it as it is")[1]


def test_the_probe_is_the_upgrade_preflight_probe():
    probe = next(t for t in _tasks(CHECK)
                 if t.get("name") == "Probe the snapshot for what this release cannot run")
    assert "roles/upgrade/files/probe_major_preflight.py" in probe["ansible.builtin.command"]["stdin"]
