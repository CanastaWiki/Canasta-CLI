"""`config refresh-template --apply` without file names is an error.

--apply overwrites the named files; with none named, the command would
silently fall back to listing drift and leave the operator thinking the
templates were applied.
"""

import os
import subprocess
import sys

import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
REFRESH = os.path.join(REPO_ROOT, "roles", "config", "tasks",
                       "refresh_template.yml")


def _run(tmp_path, extra_vars):
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {"ansible_python_interpreter": sys.executable,
                 **extra_vars},
        "tasks": [{"ansible.builtin.include_tasks": REFRESH}],
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0")
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    return subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env,
                          cwd=tmp_path)


def test_apply_without_files_fails_with_usage(tmp_path):
    proc = _run(tmp_path, {"apply": True, "files": ""})
    assert proc.returncode != 0
    assert "--apply needs the file(s) to overwrite" in proc.stdout + proc.stderr


def test_apply_with_files_passes_the_guard(tmp_path):
    proc = _run(tmp_path, {"apply": True, "files": "config/default.vcl"})
    assert "--apply needs the file(s)" not in proc.stdout + proc.stderr
