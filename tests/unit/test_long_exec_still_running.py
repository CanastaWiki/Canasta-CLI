"""A long exec still running when the wait ends is reported as such: a
failure saying it may still be running, or, when the caller gives
rx_timeout_msg, a warning with that text and the play continues."""

import os
import subprocess
import sys

import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
RESILIENT = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "resilient_exec.yml")
RUN_UPDATE = os.path.join(
    REPO_ROOT, "roles", "mediawiki", "tasks", "run_update_php.yml")
IMPORT = os.path.join(REPO_ROOT, "playbooks", "import.yml")


def _run(tmp_path, **rx_vars):
    rx = {"rx_name": "slow job", "rx_delay": 1}
    rx.update(rx_vars)
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost", "connection": "local", "gather_facts": False,
        "vars": {"ansible_python_interpreter": sys.executable},
        "tasks": [
            {"ansible.builtin.include_tasks": RESILIENT, "vars": rx},
            {"ansible.builtin.debug": {"msg": "after the job"}},
        ],
    }]))
    env = dict(
        os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
        ANSIBLE_STDOUT_CALLBACK="default",
        ANSIBLE_ASYNC_DIR=str(tmp_path / "async"),
    )
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env,
                          cwd=tmp_path)
    return proc.returncode, proc.stdout + proc.stderr


# The poll window ends while the job is still running.
UNFINISHED = {"rx_cmd": "sleep 4", "rx_async": 60, "rx_retries": 1}
# The time limit kills the job's client before the polls run out.
KILLED = {"rx_cmd": "sleep 30", "rx_async": 2, "rx_retries": 20}


def test_unfinished_job_fails_as_still_running(tmp_path):
    rc, out = _run(tmp_path, **UNFINISHED)
    assert rc != 0, out
    assert "slow job did not finish within 1 minute and may still be running" in out
    assert "rc=unknown" not in out
    assert "after the job" not in out


def test_killed_job_fails_as_still_running(tmp_path):
    rc, out = _run(tmp_path, **KILLED)
    assert rc != 0, out
    assert "slow job did not finish within 1 minute and may still be running" in out
    assert "rc=unknown" not in out


def test_unfinished_job_warns_and_continues_with_timeout_msg(tmp_path):
    rc, out = _run(tmp_path, rx_timeout_msg="Check on it later.", **UNFINISHED)
    assert rc == 0, out
    assert ("Warning: slow job did not finish within 1 minute and may still "
            "be running. Check on it later.") in out
    assert "after the job" in out


def test_killed_job_warns_and_continues_with_timeout_msg(tmp_path):
    rc, out = _run(tmp_path, rx_timeout_msg="Check on it later.", **KILLED)
    assert rc == 0, out
    assert "may still be running. Check on it later." in out
    assert "after the job" in out


def test_failed_job_still_fails_with_its_rc(tmp_path):
    rc, out = _run(tmp_path, rx_cmd="echo boom >&2; exit 3", rx_async=60,
                   rx_retries=30, rx_timeout_msg="Check on it later.")
    assert rc != 0, out
    assert "slow job failed (rc=3): boom" in out
    assert "may still be running" not in out


def test_successful_job_reports_nothing(tmp_path):
    rc, out = _run(tmp_path, rx_cmd="true", rx_async=60, rx_retries=30,
                   rx_timeout_msg="Check on it later.")
    assert rc == 0, out
    assert "may still be running" not in out


def _update_php_includes(path):
    with open(path) as f:
        tasks = yaml.safe_load(f)
    return [t for t in tasks
            if "update.php" in str((t.get("vars") or {}).get("exec_command", ""))]


def test_update_php_points_to_maintenance_update():
    for path, wiki_var in ((RUN_UPDATE, "_update_php_wiki"), (IMPORT, "wiki")):
        (inc,) = _update_php_includes(path)
        msg = " ".join(inc["vars"]["exec_long_timeout_msg"].split())
        assert ("canasta maintenance update -i {{ instance_id }} "
                "-w {{ %s }}" % wiki_var) in msg, path
