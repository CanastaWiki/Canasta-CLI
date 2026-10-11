"""Kubernetes restore safety.

- A safety snapshot is taken before the restore unless
  --skip-safety-backup is given, under the instance's restic host.
- The images PVC is not emptied before restic runs; `restic restore
  --delete` makes it match the snapshot, scoped by an include filter.
- The restore pod's wait follows canasta_k8s_backup_timeout.
- A failed or empty dump listing fails the restore instead of skipping
  the database import, and an external database server imports only the
  restored wikis' databases.
"""

import json
import os
import subprocess
import sys

import pytest
import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
RESTORE_K8S = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks", "k8s_restore_instance.yml")
RESTORE_INSTANCE = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "restore_instance.yml")
COMMON = os.path.join(REPO_ROOT, "roles", "common")


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _task(tasks, name):
    for t in tasks:
        if t.get("name") == name:
            return t
        for key in ("block", "rescue", "always"):
            if key in t:
                try:
                    return _task(t[key], name)
                except AssertionError:
                    pass
    raise AssertionError("task %r not found" % name)


# --- safety snapshot ---

def test_k8s_restore_takes_a_safety_snapshot():
    k8s = _task(_load(RESTORE_INSTANCE), "Restore (Kubernetes)")
    names = [t.get("name") for t in k8s["block"]]
    safety = names.index("Take safety backup before restore")
    restore = names.index("Restore (Kubernetes pod-based restore + DB/Secret handling)")
    assert safety < restore

    task = k8s["block"][safety]
    assert task["ansible.builtin.include_tasks"] == "run_backup.yml"
    assert "skip_safety_backup" in task["when"]
    args = task["vars"]["backup_args"]
    assert args[:2] == ["--host", "{{ instance_id }}"]
    assert args[args.index("--tag") + 1] == "safety-before-restore"
    assert args[-2:] == ["backup", "/currentsnapshot"]
    # A failed safety snapshot must stop the restore.
    assert "ignore_errors" not in task and "failed_when" not in task


# --- restic --delete instead of the pre-wipe ---

def _restore_script():
    pod = _task(_load(RESTORE_K8S), "Create restore pod")
    return pod["kubernetes.core.k8s"]["definition"]["spec"]["containers"][0]["command"][2]


def test_images_pvc_is_not_emptied_before_restic():
    assert "rm -rf" not in _restore_script()


def test_restic_restore_deletes_files_not_in_snapshot():
    script = _restore_script()
    assert "{{ _restore_include }}" in script
    assert "--delete" in script
    assert "--target /" in script


def test_full_restore_include_keeps_delete_inside_currentsnapshot():
    # restic refuses --target / --delete without a filter; the filter is
    # also what keeps the deletion off the container's own files.
    full = _task(_load(RESTORE_K8S), "Default to a full restore")
    assert full["when"] == "wiki is not defined"
    assert full["ansible.builtin.set_fact"]["_restore_include"].split() == [
        "--include", "/currentsnapshot"]


# --- timeout ---

def test_restore_wait_follows_configurable_timeout():
    tasks = _load(RESTORE_K8S)
    wait = _task(tasks, "Wait for restore to complete")
    assert "canasta_k8s_backup_timeout | default(3600)" in wait["retries"]
    assert wait["delay"] == 2
    fail = _task(tasks, "Fail when the restore did not finish in time")
    assert fail["when"] == "_restore_done.rc != 0"
    assert "may still" in fail["ansible.builtin.fail"]["msg"]


# --- dump listing and selection, run as a playbook ---

FIRST = "Fail when the DB dumps could not be listed"
LAST = "Fail when the snapshot has no DB dump to import"

WIKIS_YAML = """wikis:
- id: main
  url: example.com
  name: Main
  extra_databases: [cargo_main]
- id: docs
  url: example.com/docs
  name: Docs
"""


def _selection_tasks():
    tasks = _load(RESTORE_K8S)
    names = [t.get("name") for t in tasks]
    return tasks[names.index(FIRST):names.index(LAST) + 1]


def _run(tmp_path, listing, rc=0, external=False, wiki=None,
         incomplete=False):
    inst = tmp_path / "inst"
    (inst / "config").mkdir(parents=True)
    (inst / "config" / "wikis.yaml").write_text(WIKIS_YAML)
    play_vars = {
        "instance_path": str(inst),
        "ansible_python_interpreter": sys.executable,
        "_resolved_snapshot": "abc123",
        "_restore_incomplete": incomplete,
        "_restore_db_files": {
            "rc": rc, "stderr": "" if rc == 0 else "error: pod not found",
            "stdout_lines": listing,
        },
        "_restore_env": {"variables": {
            "USE_EXTERNAL_DB": "true" if external else "false"}},
    }
    if wiki:
        play_vars["wiki"] = wiki
    out = tmp_path / "dumps.json"
    tasks = _selection_tasks() + [{
        "name": "Record the chosen dumps",
        "ansible.builtin.copy": {
            "content": "{{ _restore_dumps | to_json }}", "dest": str(out)},
    }]
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost", "connection": "local", "gather_facts": False,
        "vars": play_vars, "tasks": tasks,
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_LIBRARY=os.path.join(COMMON, "library"),
               ANSIBLE_MODULE_UTILS=os.path.join(COMMON, "module_utils"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env,
                          cwd=tmp_path)
    proc.output = proc.stdout + proc.stderr
    dumps = json.loads(out.read_text()) if out.exists() else None
    return proc, dumps


LISTING = ["db_main.sql", "db_docs.sql", "db_cargo_main.sql",
           "db_othertenant.sql", "secrets-x.yaml"]


def test_bundled_db_imports_every_dump(tmp_path):
    proc, dumps = _run(tmp_path, LISTING)
    assert proc.returncode == 0, proc.output
    assert dumps == ["db_main.sql", "db_docs.sql", "db_cargo_main.sql",
                     "db_othertenant.sql"]


def test_external_db_imports_only_the_restored_wikis(tmp_path):
    proc, dumps = _run(tmp_path, LISTING, external=True)
    assert proc.returncode == 0, proc.output
    assert dumps == ["db_main.sql", "db_docs.sql", "db_cargo_main.sql"]
    assert "Not importing db_othertenant.sql" in proc.output


def test_failed_listing_fails_the_restore(tmp_path):
    proc, _ = _run(tmp_path, [], rc=1)
    assert proc.returncode != 0
    assert "Could not list the database dumps" in proc.output


@pytest.mark.parametrize("external", [False, True])
def test_full_restore_without_dumps_fails(tmp_path, external):
    listing = ["secrets-x.yaml"] + (["db_othertenant.sql"] if external else [])
    proc, _ = _run(tmp_path, listing, external=external)
    assert proc.returncode != 0
    assert "has no database dumps" in proc.output


def test_incomplete_snapshot_without_dumps_is_allowed(tmp_path):
    proc, dumps = _run(tmp_path, ["secrets-x.yaml"], incomplete=True)
    assert proc.returncode == 0, proc.output
    assert dumps == []


def test_single_wiki_without_its_dump_fails(tmp_path):
    proc, _ = _run(tmp_path, ["db_main.sql"], wiki="docs")
    assert proc.returncode != 0
    assert "no db_docs.sql" in proc.output


def test_single_wiki_with_its_dump_imports_it(tmp_path):
    proc, dumps = _run(tmp_path, ["db_docs.sql"], wiki="docs")
    assert proc.returncode == 0, proc.output
    assert dumps == ["db_docs.sql"]


def test_external_db_client_passes_the_port():
    target = _task(_load(RESTORE_K8S), "Choose the import target")
    login = target["ansible.builtin.set_fact"]["_restore_import_login"]
    assert '-P "${MYSQL_PORT:-3306}"' in login
