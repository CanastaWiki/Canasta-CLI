"""create keeps the MW_SECRET_KEY that .env holds before the stack starts.

On Kubernetes the <id>-mw-secrets Secret is built from .env before the
pods start, so neither the installer nor a database import may replace
the key in .env afterward.

The real _database.yml, install.yml, install_single_wiki.yml and
generate_secret_key.yml run against stubbed container execs; the stub
exec answers every command as if LocalSettings.php held a different key.
"""

import os
import shutil
import subprocess
import sys

import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
COMMON = os.path.join(REPO_ROOT, "roles", "common")
MW_TASKS = os.path.join(REPO_ROOT, "roles", "mediawiki", "tasks")
DATABASE = os.path.join(REPO_ROOT, "roles", "create", "tasks", "_database.yml")

KEY = "a" * 64
OTHER_KEY = "b" * 64


def _stub_root(tmp_path):
    root = tmp_path / "root"
    mw = root / "roles" / "mediawiki" / "tasks"
    orch = root / "roles" / "orchestrator" / "tasks"
    mw.mkdir(parents=True)
    orch.mkdir(parents=True)
    for name in ("install.yml", "install_single_wiki.yml",
                 "generate_secret_key.yml"):
        shutil.copy(os.path.join(MW_TASKS, name), mw / name)
    noop = [{"ansible.builtin.debug": {"msg": "stub"}}]
    for name in ("wait_for_db.yml", "import_database.yml",
                 "run_install_php.yml"):
        (mw / name).write_text(yaml.safe_dump(noop))
    (orch / "exec.yml").write_text(yaml.safe_dump([{
        "ansible.builtin.set_fact": {
            "exec_result": {"rc": 0, "stdout": OTHER_KEY}},
    }]))
    return root


def _run(tmp_path, extra_vars):
    root = _stub_root(tmp_path)
    inst = tmp_path / "inst"
    inst.mkdir()
    (inst / ".env").write_text(f"MW_SECRET_KEY={KEY}\n")
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {
            "instance_path": str(inst),
            "canasta_root": str(root),
            "instance_orchestrator": "kubernetes",
            "wiki": "main",
            "_created_wikis": {
                "wiki_ids": ["main"],
                "wikis": [{"id": "main", "url": "localhost/main",
                           "name": "Main"}],
            },
            "_admin_password": "admin-pw",
            "_rootdbpass": "root-pw",
            "ansible_python_interpreter": sys.executable,
            **extra_vars,
        },
        "tasks": [{"ansible.builtin.include_tasks": DATABASE}],
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_ROLES_PATH=str(root / "roles"),
               ANSIBLE_FILTER_PLUGINS=os.path.join(REPO_ROOT, "filter_plugins"),
               ANSIBLE_LIBRARY=os.path.join(COMMON, "library"),
               ANSIBLE_MODULE_UTILS=os.path.join(COMMON, "module_utils"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env,
                          cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return (inst / ".env").read_text()


class TestCreateKeepsSecretKey:
    def test_installer_keeps_the_env_key(self, tmp_path):
        env = _run(tmp_path, {})
        assert f"MW_SECRET_KEY={KEY}" in env
        assert OTHER_KEY not in env

    def test_database_import_keeps_the_env_key(self, tmp_path):
        env = _run(tmp_path, {"database": "/nonexistent/dump.sql"})
        assert f"MW_SECRET_KEY={KEY}" in env
