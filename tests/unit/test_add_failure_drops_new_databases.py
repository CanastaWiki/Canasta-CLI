"""A failed add drops only the databases it created.

The wiki is not yet in wikis.yaml when the add fails, so `canasta remove
-w` cannot reach its database. add.yml's rescue drops the wiki's database
and the extra databases it was importing, but only those that did not
exist before the add: CREATE DATABASE IF NOT EXISTS means the wiki ID may
name a database that was already there. The rescue runs here after a
forced failure, against stub db_admin and exec task files that only log.
"""

import os
import subprocess
import sys

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
ADD = os.path.join(REPO_ROOT, "playbooks", "add.yml")
PLAYBOOK = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")


def _outer_block():
    with open(ADD) as f:
        tasks = yaml.safe_load(f)
    return next(t for t in tasks
                if t.get("name") == "Create the wiki's databases and place its files")


def _walk(tasks):
    for t in tasks or []:
        yield t
        for key in ("block", "rescue", "always"):
            yield from _walk(t.get(key))


def test_database_phase_and_images_are_inside_the_block():
    names = [t.get("name") for t in _walk(_outer_block()["block"])]
    for name in ("Import database dump", "Install new wiki",
                 "Import images tarball for the new wiki",
                 "Record the databases that exist before the add"):
        assert name in names
    with open(ADD) as f:
        top = [t.get("name") for t in yaml.safe_load(f)]
    # Once the wiki is in wikis.yaml, `canasta remove -w` cleans up.
    assert "Add wiki to wikis.yaml" in top


def test_skip_install_records_nothing():
    rec = [t for t in _outer_block()["block"]
           if t.get("name") == "Record the databases that exist before the add"]
    assert rec[0]["when"] == "not (skip_install | default(false) | bool)"


def _run(tmp_path, play_vars, drop_fails=False):
    log = tmp_path / "log"
    tasks = tmp_path / "root" / "roles" / "orchestrator" / "tasks"
    tasks.mkdir(parents=True)
    (tasks / "db_admin.yml").write_text(yaml.safe_dump([{
        "ansible.builtin.set_fact": {
            "_db_admin_service": "db", "_db_admin_pwd": "PWD",
            "_db_admin_args": "-u root", "_db_admin_dotenv": {}}}]))
    (tasks / "exec.yml").write_text(yaml.safe_dump([{
        "ansible.builtin.shell": (
            "printf '%s\\n' \"$CMD\" >> " + str(log)
            + ("; exit 1" if drop_fails else "")),
        "environment": {"CMD": "{{ exec_command }}"},
    }]))
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": dict(play_vars, canasta_root=str(tmp_path / "root"),
                     ansible_python_interpreter=sys.executable,
                     wiki="second"),
        "tasks": [{
            "block": [{"ansible.builtin.fail": {"msg": "No space left"}}],
            "rescue": _outer_block()["rescue"],
        }],
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0")
    proc = subprocess.run([PLAYBOOK, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env,
                          cwd=tmp_path, stdin=subprocess.DEVNULL)
    drops = log.read_text().split() if log.exists() else []
    return proc, " ".join(drops)


def test_drops_only_databases_absent_before(tmp_path):
    proc, drops = _run(tmp_path, {
        "_add_dbs_before": ["main", "second_cargo", "mysql"],
        "_import_extra_dumps": {"second_cargo": "/d/x.second_cargo.sql",
                                "second_smw": "/d/x.second_smw.sql",
                                "second_none": ""},
    })
    assert proc.returncode != 0
    assert "DROP DATABASE IF EXISTS `second`" in drops
    assert "DROP DATABASE IF EXISTS `second_smw`" in drops
    assert "second_cargo" not in drops
    assert "second_none" not in drops
    assert "main" not in drops
    assert "Adding wiki 'second' failed." in proc.stdout
    assert "Removed the database(s) it created: second, second_smw." in (
        proc.stdout.replace("\n", " "))


def test_existing_wiki_database_is_never_dropped(tmp_path):
    proc, drops = _run(tmp_path, {"_add_dbs_before": ["main", "second"]})
    assert proc.returncode != 0
    assert drops == ""
    assert "Removed" not in proc.stdout


def test_nothing_is_dropped_when_the_databases_were_not_recorded(tmp_path):
    proc, drops = _run(tmp_path, {})
    assert proc.returncode != 0
    assert drops == ""
    assert "Adding wiki 'second' failed." in proc.stdout


def test_a_failed_drop_warns_and_still_fails(tmp_path):
    proc, drops = _run(tmp_path, {"_add_dbs_before": ["main"]},
                       drop_fails=True)
    assert proc.returncode != 0
    assert "DROP DATABASE IF EXISTS `second`" in drops
    assert "could not drop database(s) second" in proc.stdout
    assert "Removed" not in proc.stdout
