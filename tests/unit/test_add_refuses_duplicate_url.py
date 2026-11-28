"""`canasta add` refuses a URL another wiki has before it creates anything.

The wikis.yaml write after the install refuses a duplicate URL as well, but
by then the new wiki's database exists, and it is outside the rescue that
drops the databases a failed add created."""

import os
import subprocess
import sys

import pytest
import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ADD = os.path.join(REPO_ROOT, "playbooks", "add.yml")
COMMON = os.path.join(REPO_ROOT, "roles", "common")
CHECK = "Fail if another wiki already has the URL"
READ = "Read the wikis to check the sitemap paths"


def _top():
    with open(ADD) as f:
        return yaml.safe_load(f)


def _names():
    return [t.get("name") for t in _top()]


def test_the_check_runs_before_anything_is_created():
    names = _names()
    assert names.index(READ) < names.index(CHECK)
    assert names.index(CHECK) < names.index("Create the wiki's databases and place its files")


def _run(tmp_path, domain, path):
    inst = tmp_path / "inst"
    (inst / "config").mkdir(parents=True)
    (inst / "config" / "wikis.yaml").write_text(yaml.safe_dump({"wikis": [
        {"id": "main", "url": "example.com", "name": "Main"},
        {"id": "docs", "url": "example.com/docs", "name": "Docs"},
    ]}))
    tasks = [t for t in _top() if t.get("name") in (READ, CHECK)]
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost", "connection": "local", "gather_facts": False,
        "vars": {"instance_path": str(inst), "wiki": "new",
                 "_add_domain": domain, "_add_path": path,
                 "ansible_python_interpreter": sys.executable},
        "tasks": tasks,
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_LIBRARY=os.path.join(COMMON, "library"),
               ANSIBLE_MODULE_UTILS=os.path.join(COMMON, "module_utils"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    return subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env, cwd=tmp_path)


@pytest.mark.parametrize("domain, path, url", [
    ("example.com", "", "example.com"),
    ("example.com", "docs", "example.com/docs"),
    ("example.com", "/docs", "example.com/docs"),
])
def test_a_url_in_use_is_refused(tmp_path, domain, path, url):
    proc = _run(tmp_path, domain, path)
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, out
    assert "Wiki URL '%s' already exists." % url in out


@pytest.mark.parametrize("domain, path", [
    ("example.com", "new"),
    ("other.example.com", ""),
    ("example.com:8443", "docs"),
])
def test_a_new_url_passes(tmp_path, domain, path):
    proc = _run(tmp_path, domain, path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
