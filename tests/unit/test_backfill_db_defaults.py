"""Tests for backfilling the bundled-DB MYSQL_* defaults into .env.

podman-compose passes ${MYSQL_HOST:-db} through literally, so an instance
whose .env predates create pinning these keys hangs on start. The start
path's heal_mysql_defaults.yml fills them in, along with MediaWiki's
database account.
"""

import os
import subprocess
import sys

import pytest
import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
HEAL = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "heal_mysql_defaults.yml")


def _heal(tmp_path, content):
    """Run heal_mysql_defaults.yml on an .env holding content; return the
    file's lines afterwards."""
    inst = tmp_path / "inst"
    inst.mkdir()
    (inst / ".env").write_text(content)
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars_files": [os.path.join(REPO_ROOT, "vars", "mysql_defaults.yml")],
        "vars": {"instance_path": str(inst),
                 "ansible_python_interpreter": sys.executable},
        "tasks": [{"ansible.builtin.include_tasks": HEAL}],
    }]))
    common = os.path.join(REPO_ROOT, "roles", "common")
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_LIBRARY=os.path.join(common, "library"),
               ANSIBLE_MODULE_UTILS=os.path.join(common, "module_utils"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env, cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return (inst / ".env").read_text().split()


FULL = ("MYSQL_HOST=db\nMYSQL_PORT=3306\nMYSQL_USER=root\nMYSQL_SSL=false\n")


def test_missing_keys_are_added(tmp_path):
    assert sorted(_heal(tmp_path, "MYSQL_PASSWORD=pw\n")) == sorted([
        "MYSQL_PASSWORD=pw", "MYSQL_HOST=db", "MYSQL_PORT=3306",
        "MYSQL_USER=root", "MYSQL_SSL=false",
        "WIKI_DB_USER=root", "WIKI_DB_PASSWORD=pw"])


def test_existing_values_are_kept(tmp_path):
    lines = _heal(tmp_path, "MYSQL_HOST=otherdb\nMYSQL_PORT=3307\nMYSQL_USER=wiki\n")
    assert {"MYSQL_HOST=otherdb", "MYSQL_PORT=3307", "MYSQL_USER=wiki",
            "MYSQL_SSL=false"} <= set(lines)
    assert "MYSQL_HOST=db" not in lines


def test_empty_value_is_filled(tmp_path):
    lines = _heal(tmp_path, "MYSQL_HOST=\n" + FULL.split("\n", 1)[1])
    assert "MYSQL_HOST=db" in lines and "MYSQL_HOST=" not in lines


def test_external_db_gets_only_the_wiki_account_keys(tmp_path):
    content = ("USE_EXTERNAL_DB=true\nMYSQL_HOST=db.example.com\n"
               "MYSQL_USER=wikiop\nMYSQL_PASSWORD=extpw\n")
    assert sorted(_heal(tmp_path, content)) == sorted(
        content.split() + ["WIKI_DB_USER=wikiop", "WIKI_DB_PASSWORD=extpw"])


def test_root_account_password_follows_a_rotation(tmp_path):
    lines = _heal(tmp_path, FULL + "MYSQL_PASSWORD=new\nWIKI_DB_USER=root\n"
                  "WIKI_DB_PASSWORD=old\n")
    assert "WIKI_DB_PASSWORD=new" in lines
    assert "WIKI_DB_PASSWORD=old" not in lines


def test_own_account_password_is_left_alone(tmp_path):
    content = FULL + ("MYSQL_PASSWORD=rootpw\nWIKI_DB_USER=mediawiki\n"
                      "WIKI_DB_PASSWORD=wikipw\n")
    assert _heal(tmp_path, content) == content.split()


@pytest.mark.parametrize("opt_out, expected", [
    ("CANASTA_DB_ROOT_ACCOUNT=true\n", ["WIKI_DB_USER=root", "WIKI_DB_PASSWORD=rootpw"]),
    ("", ["WIKI_DB_USER=mediawiki", "WIKI_DB_PASSWORD=wikipw"]),
])
def test_opting_out_puts_a_moved_instance_back_on_root(tmp_path, opt_out, expected):
    lines = _heal(tmp_path, FULL + "MYSQL_PASSWORD=rootpw\nWIKI_DB_USER=mediawiki\n"
                  "WIKI_DB_PASSWORD=wikipw\n" + opt_out)
    assert set(expected) <= set(lines)
