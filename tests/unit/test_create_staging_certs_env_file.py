"""`canasta create -e <file>` keeps the file's CANASTA_STAGING_CERTS.

_settings_files.yml persists --staging-certs as CANASTA_STAGING_CERTS after
_envfile.yml has merged the env file into .env. It used to write the flag's
value unconditionally, so an env file that asked for staging certificates
was reset to false and the instance requested production ones, which Let's
Encrypt rate-limits. The real task files run here against a temp instance.
"""

import os
import shutil
import subprocess
import sys

import pytest
import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
COMMON = os.path.join(REPO_ROOT, "roles", "common")
TASKS = os.path.join(REPO_ROOT, "roles", "create", "tasks")
TEMPLATE_ENV = os.path.join(REPO_ROOT, "instance_template", ".env")


def _staging_tasks():
    with open(os.path.join(TASKS, "_settings_files.yml")) as f:
        return [t for t in yaml.safe_load(f)
                if "CANASTA_STAGING_CERTS" in t.get("name", "")]


def _create(tmp_path, envfile_lines, **flags):
    inst = tmp_path / "inst"
    inst.mkdir()
    shutil.copy(TEMPLATE_ENV, inst / ".env")
    envfile = tmp_path / "custom.env"
    envfile.write_text("".join(line + "\n" for line in envfile_lines))
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": dict({"instance_path": str(inst), "envfile": str(envfile),
                      "domain_name": "example.com", "_rootdbpass": "pw",
                      "_wikidbpass": "pw", "_wiki_db_user": "mediawiki",
                      "ansible_python_interpreter": sys.executable}, **flags),
        "tasks": [{"ansible.builtin.include_tasks": os.path.join(TASKS, "_envfile.yml")}]
                 + _staging_tasks(),
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_FILTER_PLUGINS=os.path.join(REPO_ROOT, "filter_plugins"),
               ANSIBLE_LIBRARY=os.path.join(COMMON, "library"),
               ANSIBLE_MODULE_UTILS=os.path.join(COMMON, "module_utils"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run(
        [playbook, "-i", "localhost,canasta_controller,", "-l", "localhost",
         str(play)], capture_output=True, text=True, env=env, cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    values = [line.split("=", 1)[1] for line in (inst / ".env").read_text().splitlines()
              if line.startswith("CANASTA_STAGING_CERTS=")]
    assert len(values) == 1, values
    return values[0]


@pytest.mark.parametrize("envfile_lines,flags,expected", [
    (["CANASTA_STAGING_CERTS=true"], {}, "true"),
    (["CANASTA_STAGING_CERTS=TRUE"], {}, "true"),
    ([], {}, "false"),
    (["CANASTA_STAGING_CERTS=false"], {}, "false"),
    ([], {"staging_certs": True}, "true"),
    (["CANASTA_STAGING_CERTS=false"], {"staging_certs": True}, "true"),
])
def test_staging_certs_from_flag_or_env_file(tmp_path, envfile_lines, flags, expected):
    assert _create(tmp_path, envfile_lines, **flags) == expected
