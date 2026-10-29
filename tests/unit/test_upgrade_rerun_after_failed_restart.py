"""A re-run of `canasta upgrade` finishes an upgrade whose restart failed.

The failed run already pointed .env at the new image, so the re-run pulls
nothing new. The restart and the steps after it are taken from the real
roles/upgrade/tasks/main.yml and run on localhost with the orchestrator's
stop/start stubbed: the first run's start fails, the second's succeeds with
no image change, and the second must restart the instance and report it
upgraded rather than up to date.
"""

import os
import subprocess
import sys

import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
UPGRADE_TASKS = os.path.join(REPO_ROOT, "roles", "upgrade", "tasks")
UPGRADE_MAIN = os.path.join(UPGRADE_TASKS, "main.yml")
RECORD = os.path.join(UPGRADE_TASKS, "record_restart_failure.yml")
FIRST_TASK = "Check for an upgrade whose restart failed"


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        yaml.safe_dump(data, f)


def _mark(name):
    return {"ansible.builtin.lineinfile": {
        "path": "{{ lookup('env', 'MARKS') }}/" + name,
        "line": "{{ instance_id }}", "create": True, "mode": "0644"}}


def _harness(tmp_path):
    root = tmp_path / "root"
    orch = root / "roles" / "orchestrator" / "tasks"
    _write(str(orch / "stop.yml"), [{"name": "Stub stop", **_mark("stopped")}])
    _write(str(orch / "start.yml"), [
        {"name": "Stub start failure", "ansible.builtin.fail": {
            "msg": "The 'web' container did not report healthy."},
         "when": "lookup('env', 'FAIL_START') == '1'"},
        {"name": "Stub start", **_mark("started")},
    ])
    for name in ("exec.yml", "rerender_compose_layers.yml", "reload_caddy.yml"):
        _write(str(orch / name), [{"ansible.builtin.meta": "noop"}])
    _write(str(root / "roles" / "mediawiki" / "tasks"
               / "report_composer_failure.yml"),
           [{"ansible.builtin.meta": "noop"}])

    with open(UPGRADE_MAIN) as f:
        tasks = yaml.safe_load(f)
    names = [t.get("name") for t in tasks]
    start = names.index(FIRST_TASK) if FIRST_TASK in names else names.index(
        "Decide whether a restart is needed")
    upgrade = tmp_path / "upgrade"
    _write(str(upgrade / "instance.yml"), tasks[start:])
    _write(str(upgrade / "migrate_wiki_db_account.yml"),
           [{"name": "Stub DB account move", **_mark("db_account")}])
    _write(str(upgrade / "tidy_composer_local.yml"),
           [{"name": "Stub composer tidy", **_mark("tidy")}])
    _write(str(upgrade / "warn_overrides.yml"),
           [{"ansible.builtin.meta": "noop"}])
    with open(RECORD) as src:
        (upgrade / "record_restart_failure.yml").write_text(src.read())

    inst = tmp_path / "inst"
    inst.mkdir()
    marks = tmp_path / "marks"
    marks.mkdir()
    play = tmp_path / "play.yml"
    _write(str(play), [{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {
            "ansible_python_interpreter": sys.executable,
            "canasta_root": str(root),
            "instance_path": str(inst),
            "instance_id": "first",
            "instance_is_k8s": False,
        },
        "tasks": [{
            "name": "Upgrade the instance",
            "ansible.builtin.include_tasks": str(upgrade / "instance.yml"),
            "vars": {
                "_images_updated": "{{ lookup('env', 'IMAGES_UPDATED') == '1' }}",
                "_upgrade_rebuilt_image_changed": False,
                "_was_running": True,
                "_upgrade_stage_only": False,
                "_upgrade_build_failed_services": [],
            }}],
    }])
    collections = tmp_path / "collections"
    collections.mkdir()
    return root, play, inst, marks, collections


def _run(setup, *, fail_start, images_updated):
    root, play, _, marks, collections = setup
    env = {k: v for k, v in os.environ.items() if not k.startswith("ANSIBLE_")}
    env.update(ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_COLLECTIONS_PATH=str(collections),
               ANSIBLE_ROLES_PATH=str(root / "roles"),
               ANSIBLE_FILTER_PLUGINS=os.path.join(REPO_ROOT, "filter_plugins"),
               ANSIBLE_LOCAL_TEMP=str(play.parent / "ansible-tmp"),
               MARKS=str(marks),
               FAIL_START="1" if fail_start else "0",
               IMAGES_UPDATED="1" if images_updated else "0")
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env,
                          cwd=play.parent)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    return out


def _started(marks):
    path = marks / "started"
    return len(path.read_text().split()) if path.exists() else 0


def test_rerun_reports_the_upgrade_it_completed(tmp_path):
    setup = _harness(tmp_path)
    _, _, inst, marks, _ = setup

    out = _run(setup, fail_start=True, images_updated=True)
    assert "Canasta upgraded successfully!" not in out, out
    assert (inst / ".upgrade-incomplete").exists(), out

    out = _run(setup, fail_start=False, images_updated=False)
    assert _started(marks) == 1, out
    assert "Canasta upgraded successfully!" in out, out
    assert "Already up to date" not in out, out
    assert not (inst / ".upgrade-incomplete").exists(), out
    assert (marks / "db_account").read_text().split() == ["first"], out

    out = _run(setup, fail_start=False, images_updated=False)
    assert _started(marks) == 1, out
    assert "Already up to date — no restart needed." in out, out


def test_a_rerun_that_fails_again_keeps_the_marker(tmp_path):
    setup = _harness(tmp_path)
    _, _, inst, _, _ = setup
    _run(setup, fail_start=True, images_updated=True)
    out = _run(setup, fail_start=True, images_updated=False)
    assert "Already up to date" not in out, out
    assert "Restarting 'first' failed" in out, out
    assert (inst / ".upgrade-incomplete").exists(), out


def test_a_successful_upgrade_leaves_no_marker(tmp_path):
    setup = _harness(tmp_path)
    _, _, inst, _, _ = setup
    out = _run(setup, fail_start=False, images_updated=True)
    assert "Canasta upgraded successfully!" in out, out
    assert not (inst / ".upgrade-incomplete").exists(), out
