"""A Kubernetes create on root gives MediaWiki's own account its own password.

While MediaWiki uses root it reads MYSQL_PASSWORD, so the db Secret's
WIKI_DB_PASSWORD is unused until an upgrade moves MediaWiki to its own
account, and that move keeps the key's value. Create therefore never puts
root's password there.
"""

import json
import os
import subprocess
import sys

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
SETUP = os.path.join(REPO_ROOT, "roles", "create", "tasks",
                     "_kubernetes_setup.yml")
GENERATE = "Generate a password for MediaWiki's own account"
CHOOSE = "Choose the Secret's password for MediaWiki's own account"


def _tasks():
    with open(SETUP) as f:
        return yaml.safe_load(f)


def _choose(tmp_path, user, wikidbpass, rootdbpass):
    by_name = {t.get("name"): t for t in _tasks()}
    # Outside its role, the helper's relative path needs anchoring.
    generate = dict(by_name[GENERATE], **{"ansible.builtin.include_tasks":
                    os.path.join(os.path.dirname(SETUP),
                                 by_name[GENERATE]["ansible.builtin.include_tasks"])})
    out = tmp_path / "out.json"
    play = [{
        "hosts": "localhost", "connection": "local", "gather_facts": False,
        "vars": {"ansible_python_interpreter": sys.executable,
                 "canasta_root": REPO_ROOT,
                 "_wiki_db_user": user,
                 "_wikidbpass": wikidbpass, "_rootdbpass": rootdbpass},
        "tasks": [generate, by_name[CHOOSE],
                  {"ansible.builtin.copy": {
                      "content": "{{ {'pw': _k8s_wikidbpass} | to_json }}",
                      "dest": str(out)}}],
    }]
    path = tmp_path / "play.yml"
    path.write_text(yaml.safe_dump(play))
    base = {k: v for k, v in os.environ.items() if not k.startswith("ANSIBLE_")}
    env = dict(base, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_COLLECTIONS_PATH=str(tmp_path / "no-collections"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(path)],
                          capture_output=True, text=True, env=env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return json.loads(out.read_text())["pw"]


def test_root_create_gets_a_password_of_its_own(tmp_path):
    pw = _choose(tmp_path, "root", "rootpw", "rootpw")
    assert pw != "rootpw"
    assert len(pw) == 30


def test_own_account_create_keeps_its_password(tmp_path):
    assert _choose(tmp_path, "mediawiki", "wikipw", "rootpw") == "wikipw"


def test_own_account_keeps_a_password_equal_to_roots(tmp_path):
    assert _choose(tmp_path, "mediawiki", "samepw", "samepw") == "samepw"


def test_the_secret_gets_the_chosen_password():
    apply = next(t for t in _tasks() if t.get("name") == "Apply K8s secrets")
    assert apply["vars"]["_wikidbpass"] == "{{ _k8s_wikidbpass }}"
    names = [t.get("name") for t in _tasks()]
    assert names.index(CHOOSE) < names.index("Apply K8s secrets")
