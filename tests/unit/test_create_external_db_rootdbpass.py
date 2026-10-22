"""--rootdbpass reaches an external database's .env entry, and MediaWiki
gets its own account there.

instance_template/.env ships MYSQL_PASSWORD= empty, so after the -e file is
merged the key is always present. Only a non-empty value from the -e file
may replace --rootdbpass. On Compose, MediaWiki keeps the account and
password _passwords.yml chose, named for the instance unless --wikidbuser
names one; on Kubernetes it uses the administrator account. The real
_envfile.yml runs here against a copy of the template.
"""

import json
import os
import shutil
import subprocess
import sys

import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
COMMON = os.path.join(REPO_ROOT, "roles", "common")
ENVFILE = os.path.join(REPO_ROOT, "roles", "create", "tasks", "_envfile.yml")
TEMPLATE_ENV = os.path.join(REPO_ROOT, "instance_template", ".env")

CLI_PW = "cli-Pw-1234"
FILE_PW = "file-Pw-5678"


def _run(tmp_path, envfile_lines, k8s=False, wikidbuser=""):
    inst = tmp_path / "inst"
    inst.mkdir()
    shutil.copy(TEMPLATE_ENV, inst / ".env")
    envfile = tmp_path / "custom.env"
    envfile.write_text("".join(line + "\n" for line in envfile_lines))
    out = tmp_path / "out.json"
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {
            "instance_path": str(inst),
            "canasta_root": REPO_ROOT,
            "envfile": str(envfile),
            "_rootdbpass": CLI_PW,
            "_wikidbpass": "wiki-pw",
            "_wiki_db_user": "mediawiki",
            "id": "my-site",
            "instance_is_k8s": k8s,
            "wikidbuser": wikidbuser,
            "ansible_python_interpreter": sys.executable,
        },
        "tasks": [
            {"ansible.builtin.include_tasks": ENVFILE},
            {"ansible.builtin.copy": {
                "content": "{{ {'root': _rootdbpass, 'wiki': _wikidbpass,"
                           " 'user': _wiki_db_user} | to_json }}",
                "dest": str(out)}},
        ],
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_FILTER_PLUGINS=os.path.join(REPO_ROOT, "filter_plugins"),
               ANSIBLE_LIBRARY=os.path.join(COMMON, "library"),
               ANSIBLE_MODULE_UTILS=os.path.join(COMMON, "module_utils"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run(
        [playbook, "-i", "localhost,canasta_controller,", "-l", "localhost",
         str(play)],
        capture_output=True, text=True, env=env, cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    for pw in (CLI_PW, FILE_PW):
        assert pw not in proc.stdout
    return json.loads(out.read_text())


EXTERNAL = ["USE_EXTERNAL_DB=true", "MYSQL_HOST=db.example.com",
            "MYSQL_USER=admin", f"MYSQL_PASSWORD={FILE_PW}"]


class TestExternalDb:
    def test_rootdbpass_survives_env_file_without_password(self, tmp_path):
        facts = _run(tmp_path, ["USE_EXTERNAL_DB=true",
                                "MYSQL_HOST=db.example.com"])
        assert facts == {"root": CLI_PW, "wiki": "wiki-pw",
                         "user": "mediawiki_my_site"}

    def test_mediawiki_gets_its_own_account(self, tmp_path):
        facts = _run(tmp_path, EXTERNAL)
        assert facts == {"root": FILE_PW, "wiki": "wiki-pw",
                         "user": "mediawiki_my_site"}

    def test_wikidbuser_names_the_account(self, tmp_path):
        facts = _run(tmp_path, EXTERNAL, wikidbuser="wikiacct")
        assert facts == {"root": FILE_PW, "wiki": "wiki-pw",
                         "user": "wikiacct"}

    def test_wikidbuser_root_keeps_the_administrator_account(self, tmp_path):
        facts = _run(tmp_path, EXTERNAL, wikidbuser="root")
        assert facts == {"root": FILE_PW, "wiki": FILE_PW, "user": "admin"}

    def test_kubernetes_uses_the_administrator_account(self, tmp_path):
        facts = _run(tmp_path, EXTERNAL, k8s=True)
        assert facts == {"root": FILE_PW, "wiki": FILE_PW, "user": "admin"}


class TestBundledDb:
    def test_bundled_db_keeps_resolved_passwords(self, tmp_path):
        facts = _run(tmp_path, [f"MYSQL_PASSWORD={FILE_PW}"])
        assert facts == {"root": CLI_PW, "wiki": "wiki-pw",
                         "user": "mediawiki"}
