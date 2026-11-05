"""upgrade refuses an instance whose .env lost MYSQL_PASSWORD before it
stops anything.

The start step refuses such an instance when a database volume already
exists, and by then the upgrade has stopped it, so the wiki stays down.
The check now runs with the upgrade's other preflight checks and leaves
the instance running.

Runs the preflight tasks of the real _upgrade_single.yml against a stubbed
runtime.
"""

import os
import stat
import subprocess
import sys

import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SINGLE = os.path.join(REPO_ROOT, "playbooks", "_upgrade_single.yml")
COMMON = os.path.join(REPO_ROOT, "roles", "common")

PREFLIGHT = [
    "Reset the database password check for this instance",
    "Check that .env still has the database password",
    "Skip an instance whose database password is missing",
    "Decide whether the instance upgrades",
]

# `volume inspect` succeeds when STUB_VOLUME is set.
RUNTIME = r"""#!/bin/sh
case "$1 $2" in
  "volume inspect") [ -n "$STUB_VOLUME" ] ;;
  *) exit 2 ;;
esac
"""


def _single():
    with open(SINGLE) as f:
        return yaml.safe_load(f)


def _run(tmp_path, env_content, volume):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    runtime = bin_dir / "docker"
    runtime.write_text(RUNTIME)
    runtime.chmod(runtime.stat().st_mode | stat.S_IEXEC)
    site = tmp_path / "site"
    site.mkdir()
    (site / ".env").write_text(env_content)

    tasks = [t for t in _single() if t.get("name") in PREFLIGHT]
    assert [t["name"] for t in tasks] == PREFLIGHT
    tasks.append({"ansible.builtin.debug": {"msg": (
        "SKIP=<<{{ _upgrade_skip }}>> FAILURES=<<{{ _upgrade_failures"
        " | default([]) | join('|') }}>>")}})
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {"canasta_root": REPO_ROOT, "instance_id": "site",
                 "instance_path": str(site), "inspect_command": "docker",
                 "instance_is_k8s": False, "unfinished_pull": False,
                 "_upgrade_caddy_tls": {}, "_upgrade_space_reason": "",
                 "ansible_python_interpreter": sys.executable},
        "tasks": tasks,
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               PATH="%s:%s" % (bin_dir, os.environ["PATH"]),
               STUB_VOLUME="1" if volume else "",
               ANSIBLE_LIBRARY=os.path.join(COMMON, "library"),
               ANSIBLE_MODULE_UTILS=os.path.join(COMMON, "module_utils"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env, cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    skip = proc.stdout.split("SKIP=<<", 1)[1].split(">>", 1)[0]
    failures = proc.stdout.split("FAILURES=<<", 1)[1].split(">>", 1)[0]
    return skip, failures, (site / ".env").read_text()


class TestTheCheck:
    def test_a_lost_password_with_a_database_skips_the_instance(self, tmp_path):
        skip, failures, env = _run(tmp_path, "MW_SITE_SERVER=x\n", volume=True)
        assert skip == "True"
        assert "'site': not upgraded, because MYSQL_PASSWORD is missing" in failures
        assert "MYSQL_PASSWORD" not in env

    def test_a_lost_password_on_an_external_database_skips_the_instance(
            self, tmp_path):
        skip, failures, _ = _run(
            tmp_path, "USE_EXTERNAL_DB=true\n", volume=False)
        assert skip == "True"
        assert "external database" in failures

    def test_a_password_present_goes_ahead(self, tmp_path):
        skip, failures, _ = _run(
            tmp_path, "MYSQL_PASSWORD=secret\n", volume=True)
        assert skip == "False"
        assert failures == ""

    def test_no_database_yet_heals_and_goes_ahead(self, tmp_path):
        skip, failures, env = _run(tmp_path, "MW_SITE_SERVER=x\n", volume=False)
        assert skip == "False"
        assert failures == ""
        assert "MYSQL_PASSWORD=" in env


class TestTheOrder:
    def test_the_check_runs_before_the_upgrade_role(self):
        names = [t.get("name") for t in _single()]
        assert names.index("Check that .env still has the database password") < (
            names.index("Run upgrade role (migrations + refresh + restart)"))
