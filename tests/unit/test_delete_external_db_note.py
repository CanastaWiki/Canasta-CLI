"""delete names the databases it leaves on an external database server.

Deleting an instance tears down its containers and files, but an external
database server is not the instance's to clean up, so its databases stay.
delete says so, naming the server and the databases, and says nothing for
an instance whose database ran in its own container.
"""

import os
import subprocess
import sys

import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DELETE = os.path.join(REPO_ROOT, "roles", "delete", "tasks", "main.yml")
COMMON = os.path.join(REPO_ROOT, "roles", "common")
TASKS = (
    "Read the database settings before teardown",
    "Note whether the instance uses an external database",
    "List the databases the external server holds for this instance",
    "Report the databases left on the external server",
)
WIKIS = (
    "wikis:\n"
    "- id: main\n  url: example.com\n  name: Main\n"
    "- id: docs\n  url: example.com/docs\n  name: Docs\n"
    "  extra_databases: [docs_shared]\n"
)


def _note(tmp_path, env):
    inst = tmp_path / "inst"
    (inst / "config").mkdir(parents=True)
    (inst / ".env").write_text(env)
    (inst / "config" / "wikis.yaml").write_text(WIKIS)
    with open(DELETE) as f:
        by_name = {t.get("name"): t for t in yaml.safe_load(f)
                   if isinstance(t, dict)}
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost", "connection": "local", "gather_facts": False,
        "vars": {"instance_path": str(inst),
                 "ansible_python_interpreter": sys.executable},
        "tasks": [by_name[n] for n in TASKS],
    }]))
    env_vars = dict(os.environ, ANSIBLE_NOCOLOR="1",
                    ANSIBLE_LOCALHOST_WARNING="0",
                    ANSIBLE_LOCAL_TEMP=str(tmp_path / "ansible-tmp"),
                    ANSIBLE_LIBRARY=os.path.join(COMMON, "library"),
                    ANSIBLE_MODULE_UTILS=os.path.join(COMMON, "module_utils"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env_vars,
                          cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc.stdout


def test_external_database_names_the_server_and_databases(tmp_path):
    out = _note(tmp_path, "USE_EXTERNAL_DB=true\nMYSQL_HOST=db.example.com\n"
                          "MYSQL_PASSWORD=secret\n")
    assert ("The instance's databases on the external server db.example.com "
            "were not dropped: main, docs, docs_shared.") in out
    assert "secret" not in out


def test_internal_database_says_nothing(tmp_path):
    out = _note(tmp_path, "MYSQL_PASSWORD=secret\n")
    assert "were not dropped" not in out
