"""canasta schedule add/list/remove/run.

The pure logic (validation, the crontab block, the list table) is tested
directly; the crontab sync and `schedule run` are run as real playbooks
against a host crontab file and a stand-in compose command.
"""

import os
import shutil
import subprocess
import sys
import textwrap

import pytest
import yaml

from canasta_schedule import (
    backup_cron,
    cron_line,
    dump,
    installed,
    is_active,
    parse,
    render_table,
    rewrite_crontab,
    validate_cron,
    validate_entry,
    validate_name,
)
from _orchestrator import INSTANCE_IS_K8S_VARS

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
COMMON = os.path.join(REPO_ROOT, "roles", "common")
TASKS = os.path.join(REPO_ROOT, "roles", "schedule", "tasks")

ENTRY = {"cron": "0 3 * * *", "service": "web", "command": "php run.php x"}


# --- validation --------------------------------------------------------------

@pytest.mark.parametrize("name", ["a", "cache-cleanup", "x1", "a" * 40])
def test_valid_names(name):
    assert validate_name(name) is None


@pytest.mark.parametrize("name", [
    "", "-a", "a-", "Cache", "a_b", "a b", "a;b", "a" * 41, "../x"])
def test_invalid_names(name):
    assert validate_name(name)


def test_backup_name_is_reserved():
    assert "reserved" in validate_name("backup")


@pytest.mark.parametrize("expr", [
    "* * * * *", "0 3 * * 1-5", "*/15 2,4 1 jan mon"])
def test_valid_cron(expr):
    assert validate_cron(expr) is None


@pytest.mark.parametrize("expr", [
    "* * * *", "0 0 3 * * *", "@daily", "* * * * * ; rm -rf /",
    "* * * * *\n* * * * * x", "$(x) * * * *"])
def test_invalid_cron(expr):
    assert validate_cron(expr)


def test_entry_rejects_unknown_keys_and_bad_fields():
    errors = validate_entry("x", {"cron": "bad", "service": "W EB",
                                  "command": " ", "hosts": [],
                                  "extra": 1})
    text = " ".join(errors)
    for part in ("unknown keys: extra", "cron expression", "invalid service",
                 "command must", "hosts must"):
        assert part in text


def test_parse_refuses_a_bad_file_whole():
    with pytest.raises(ValueError) as exc:
        parse("good:\n  cron: '* * * * *'\n  command: x\nBad:\n  cron: x\n")
    assert "Bad" in str(exc.value)


def test_parse_empty_and_dump_round_trip():
    assert parse("") == {}
    assert parse("\n") == {}
    schedules = {"b": dict(ENTRY), "a": dict(ENTRY, hosts=["prod"])}
    text = dump(schedules)
    assert text.index("a:") < text.index("b:")
    assert parse(text) == schedules


# --- host targeting ----------------------------------------------------------

def test_is_active():
    assert is_active(ENTRY, "")
    assert is_active(ENTRY, "staging")
    limited = dict(ENTRY, hosts=["prod"])
    assert is_active(limited, "prod")
    assert not is_active(limited, "staging")
    # A clone with no gitops identity never runs a host-limited schedule.
    assert not is_active(limited, "")


# --- crontab -----------------------------------------------------------------

def test_cron_line_quotes_and_escapes_percent():
    line = cron_line("x", dict(ENTRY, cron="0  3 * * *"), "site",
                     "/srv/my site%", "/usr/local/bin/canasta",
                     "CANASTA_CONFIG_DIR=/etc/c ")
    assert line.startswith("0 3 * * * PATH=/usr/local/sbin:/usr/local/bin:"
                           "/usr/sbin:/usr/bin:/sbin:/bin "
                           "CANASTA_CONFIG_DIR=/etc/c "
                           "/usr/local/bin/canasta schedule run -i site "
                           "--name x >> ")
    assert "'/srv/my site\\%/schedule-x.log'" in line
    # The user's command never reaches the crontab.
    assert "php" not in line


def test_rewrite_replaces_block_and_keeps_others():
    other = "5 5 * * * /usr/bin/true\n"
    text = rewrite_crontab(other, "site", ["L1"])
    assert text == (other + "# BEGIN canasta-schedule-site\nL1\n"
                    "# END canasta-schedule-site\n")
    text = rewrite_crontab(text, "site", ["L2", "L3"])
    assert "L1" not in text and "L2\nL3\n" in text
    assert text.count("BEGIN canasta-schedule-site") == 1
    assert rewrite_crontab(text, "site", []) == other


def test_rewrite_sweeps_stray_lines_but_not_other_instances():
    text = ("1 1 * * * canasta schedule run -i site --name old >> l 2>&1\n"
            "1 1 * * * canasta schedule run -i site2 --name x >> l 2>&1\n")
    new = rewrite_crontab(text, "site", [])
    assert "-i site --name old" not in new
    assert "-i site2 --name x" in new


def test_rewrite_empty_crontab_stays_empty():
    assert rewrite_crontab("", "site", []) == ""


def test_installed_and_backup_cron():
    text = rewrite_crontab(
        "0 2 * * * canasta backup create -i site --tag scheduled\n", "site",
        [cron_line("x", ENTRY, "site", "/srv", "canasta")])
    assert installed(text, "site") == {"x": ["0 3 * * *"]}
    assert installed(text, "sit") == {}
    assert backup_cron(text, "site") == "0 2 * * *"
    assert backup_cron(text, "sit") == ""


# --- list --------------------------------------------------------------------

def test_render_table_rows_and_drift():
    schedules = {"a": dict(ENTRY), "b": dict(ENTRY, hosts=["prod"]),
                 "c": dict(ENTRY)}
    crontab = rewrite_crontab(
        "0 2 * * * canasta backup create -i site --tag scheduled\n", "site",
        [cron_line(n, ENTRY, "site", "/srv", "canasta") for n in ("a", "z")])
    states = {"a": {"started": "2026-11-22T03:00:00Z", "result": "failed",
                    "rc": 2}}
    out = render_table(schedules, crontab, states, "site", "staging")
    lines = out.splitlines()
    assert lines[0].split()[:2] == ["NAME", "SERVICE"]
    assert lines[1].startswith("backup ")
    assert "exit 2" in out and "never" in out
    assert "'b' runs only on gitops host(s) prod" in out
    assert "'c' is not installed" in out
    assert "Crontab entry for 'z' has no schedule" in out


def test_render_table_empty():
    assert render_table({}, "", {}, "site", "") == (
        "No schedules for instance 'site'.")


# --- playbooks ---------------------------------------------------------------

def _play(tmp_path, tasks, play_vars, env_extra=None):
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost", "connection": "local", "gather_facts": False,
        "vars": dict(INSTANCE_IS_K8S_VARS, canasta_root=REPO_ROOT,
                     ansible_python_interpreter=sys.executable,
                     **play_vars),
        "tasks": tasks,
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_LOCAL_TEMP=str(tmp_path / "ansible-tmp"),
               ANSIBLE_FILTER_PLUGINS=os.path.join(REPO_ROOT, "filter_plugins"),
               ANSIBLE_LIBRARY=os.path.join(COMMON, "library"),
               ANSIBLE_MODULE_UTILS=os.path.join(COMMON, "module_utils"),
               CANASTA_CONFIG_DIR="")
    env.update(env_extra or {})
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    return subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env,
                          cwd=tmp_path)


def _instance(tmp_path, schedules=None, gitops_host=None):
    inst = tmp_path / "inst"
    (inst / "config").mkdir(parents=True)
    if schedules is not None:
        (inst / "config" / "schedules.yml").write_text(dump(schedules))
    if gitops_host:
        (inst / ".gitops-host").write_text(gitops_host + "\n")
    return inst


def _sync(tmp_path, inst, crontab):
    return _play(
        tmp_path,
        [{"ansible.builtin.include_tasks": os.path.join(TASKS, "sync.yml")}],
        {"instance_id": "site", "instance_path": str(inst),
         "canasta_cli_bin": "/usr/local/bin/canasta"},
        {"CANASTA_HOST_CRONTAB": str(crontab)})


def test_sync_installs_active_schedules_and_removes_the_rest(tmp_path):
    inst = _instance(tmp_path, {"a": dict(ENTRY),
                                "b": dict(ENTRY, hosts=["prod"])},
                     gitops_host="staging")
    crontab = tmp_path / "crontab"
    crontab.write_text("5 5 * * * /usr/bin/true\n")
    proc = _sync(tmp_path, inst, crontab)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    text = crontab.read_text()
    assert text.startswith("5 5 * * * /usr/bin/true\n")
    assert installed(text, "site") == {"a": ["0 3 * * *"]}
    assert "/usr/local/bin/canasta schedule run -i site --name a" in text

    (inst / ".gitops-host").write_text("prod\n")
    assert _sync(tmp_path, inst, crontab).returncode == 0
    assert set(installed(crontab.read_text(), "site")) == {"a", "b"}

    (inst / "config" / "schedules.yml").unlink()
    assert _sync(tmp_path, inst, crontab).returncode == 0
    assert crontab.read_text() == "5 5 * * * /usr/bin/true\n"


def test_sync_refuses_a_bad_schedules_file(tmp_path):
    inst = _instance(tmp_path)
    (inst / "config" / "schedules.yml").write_text(
        "x:\n  cron: '* * * * * ; rm -rf /'\n  command: y\n")
    crontab = tmp_path / "crontab"
    crontab.write_text("")
    proc = _sync(tmp_path, inst, crontab)
    assert proc.returncode != 0
    assert "cron expression" in proc.stdout + proc.stderr
    assert crontab.read_text() == ""


def _module(tmp_path, args):
    return _play(tmp_path, [{"canasta_schedules": args,
                             "register": "r"},
                            {"ansible.builtin.debug": {"var": "r"}}], {})


def test_add_refuses_a_duplicate_and_remove_drops_the_file(tmp_path):
    inst = _instance(tmp_path, {"a": dict(ENTRY)})
    proc = _module(tmp_path, {"action": "add", "instance_id": "site",
                              "instance_path": str(inst), "name": "a",
                              "cron": "* * * * *", "command": "x"})
    assert proc.returncode != 0
    assert "already exists" in proc.stdout + proc.stderr
    proc = _module(tmp_path, {"action": "add", "instance_id": "site",
                              "instance_path": str(inst), "name": "b",
                              "cron": "* * * * *", "command": "x",
                              "hosts": ["prod"]})
    assert proc.returncode == 0, proc.stdout
    data = yaml.safe_load((inst / "config" / "schedules.yml").read_text())
    assert data["b"] == {"cron": "* * * * *", "service": "web",
                         "command": "x", "hosts": ["prod"]}
    for name in ("a", "b"):
        proc = _module(tmp_path, {"action": "remove", "instance_id": "site",
                                  "instance_path": str(inst), "name": name})
        assert proc.returncode == 0, proc.stdout
    assert not (inst / "config" / "schedules.yml").exists()


def test_clear_removes_only_this_instance(tmp_path):
    crontab = tmp_path / "crontab"
    crontab.write_text(rewrite_crontab(
        rewrite_crontab("5 5 * * * /usr/bin/true\n", "site2", ["L2"]),
        "site", ["L1"]))
    proc = _module(tmp_path, {"action": "clear", "instance_id": "site",
                              "crontab_file": str(crontab)})
    assert proc.returncode == 0, proc.stdout
    text = crontab.read_text()
    assert "L1" not in text and "L2" in text and "/usr/bin/true" in text


# --- schedule run ------------------------------------------------------------

def _run_tasks():
    with open(os.path.join(TASKS, "run.yml")) as f:
        tasks = yaml.safe_load(f)
    # The instance comes from the play vars instead of the registry.
    assert tasks[0]["name"] == "Resolve instance"
    return tasks[1:]


@pytest.fixture
def fake_compose(tmp_path):
    """A compose command that runs `exec -T [--user U] <service> <cmd...>`
    locally, recording the user and service it was asked for."""
    script = tmp_path / "fake-compose"
    script.write_text(textwrap.dedent("""\
        #!/bin/sh
        [ "$1" = exec ] && [ "$2" = -T ] || exit 99
        shift 2
        user=-
        if [ "$1" = --user ]; then user=$2; shift 2; fi
        echo "$user $1" > "$FAKE_SERVICE_LOG"
        shift
        exec "$@"
        """))
    script.chmod(0o755)
    return script


# The CLI's own output callback, so a raw task error shows as it would.
CLI_CALLBACK = {
    "ANSIBLE_STDOUT_CALLBACK": "canasta_minimal",
    "ANSIBLE_CALLBACK_PLUGINS": os.path.join(REPO_ROOT, "callback_plugins"),
}


def _run(tmp_path, inst, fake_compose, name="a"):
    return _play(tmp_path, _run_tasks(),
                 {"instance_id": "site", "instance_path": str(inst),
                  "schedule_name": name, "compose_command": str(fake_compose)},
                 dict(CLI_CALLBACK,
                      FAKE_SERVICE_LOG=str(tmp_path / "service")))


def _state(inst, name="a"):
    return yaml.safe_load(
        (inst / ".schedule-state" / ("%s.yml" % name)).read_text())


def test_run_executes_the_command_and_records_success(tmp_path, fake_compose):
    marker = tmp_path / "ran"
    inst = _instance(tmp_path, {"a": dict(
        ENTRY, service="jobrunner",
        command="echo hello > %s && echo out" % marker)})
    proc = _run(tmp_path, inst, fake_compose)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert marker.read_text() == "hello\n"
    assert (tmp_path / "service").read_text() == "- jobrunner\n"
    assert "out" in proc.stdout
    state = _state(inst)
    assert state["result"] == "ok" and state["rc"] == 0
    assert state["started"].endswith("Z")


def test_run_uses_the_web_user_in_the_web_container(tmp_path, fake_compose):
    inst = _instance(tmp_path, {"a": dict(ENTRY, command="true")})
    proc = _run(tmp_path, inst, fake_compose)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (tmp_path / "service").read_text() == "www-data web\n"


def test_run_fails_with_the_command_status(tmp_path, fake_compose):
    inst = _instance(tmp_path, {"a": dict(
        ENTRY, command="echo failing; echo oops >&2; exit 3")})
    proc = _run(tmp_path, inst, fake_compose)
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0
    assert "failed with exit status 3" in out and "oops" in out
    # The command's output is its output, not a raw task error.
    assert "failing" in out
    assert "Error: failing" not in out
    assert "non-zero return code" not in out
    assert _state(inst) == dict(_state(inst), result="failed", rc=3)


def test_run_refuses_an_unknown_schedule(tmp_path, fake_compose):
    inst = _instance(tmp_path, {"a": dict(ENTRY)})
    proc = _run(tmp_path, inst, fake_compose, name="nope")
    assert proc.returncode != 0
    assert "No schedule named 'nope'" in proc.stdout + proc.stderr


@pytest.mark.skipif(shutil.which("flock") is None, reason="needs flock")
def test_run_skips_while_the_previous_run_holds_the_lock(tmp_path,
                                                         fake_compose):
    marker = tmp_path / "ran"
    inst = _instance(tmp_path, {"a": dict(ENTRY,
                                          command="touch %s" % marker)})
    state_dir = inst / ".schedule-state"
    state_dir.mkdir()
    (state_dir / "a.yml").write_text("result: ok\nrc: 0\nstarted: earlier\n")
    holder = subprocess.Popen(["flock", str(state_dir / "a.lock"),
                               "sleep", "30"])
    try:
        for _ in range(50):
            if (state_dir / "a.lock").exists():
                break
            subprocess.run(["sleep", "0.1"])
        proc = _run(tmp_path, inst, fake_compose)
    finally:
        holder.kill()
    assert proc.returncode == 0, proc.stdout + proc.stderr
    out = proc.stdout + proc.stderr
    assert "previous run is still in progress" in out
    assert "flock" not in out and "Error" not in out
    assert not marker.exists()
    assert _state(inst)["started"] == "earlier"


# --- wiring ------------------------------------------------------------------

def _tasks(*parts):
    with open(os.path.join(REPO_ROOT, *parts)) as f:
        return yaml.safe_load(f)


def _includes_sync(task):
    inc = task.get("ansible.builtin.include_tasks") or ""
    return "roles/schedule/tasks/sync.yml" in str(inc)


def test_pull_compose_resyncs_when_the_file_changed():
    hits = [t for t in _tasks("roles", "gitops", "tasks", "pull_compose.yml")
            if _includes_sync(t)]
    assert hits and "config/schedules.yml" in str(hits[0]["when"])


def test_restore_resyncs():
    assert any(_includes_sync(t) for t in
               _tasks("roles", "backup", "tasks", "finish_restore.yml"))


def test_delete_clears_the_crontab():
    hits = [t for t in _tasks("roles", "delete", "tasks", "main.yml")
            if (t.get("canasta_schedules") or {}).get("action") == "clear"]
    assert hits


def test_add_and_remove_stage_for_gitops_then_sync():
    for action in ("add", "remove"):
        includes = [str(t.get("ansible.builtin.include_tasks", ""))
                    for t in _tasks("roles", "schedule", "tasks",
                                    action + ".yml")]
        stage = next(i for i, s in enumerate(includes)
                     if "stage_schedules.yml" in s)
        sync = next(i for i, s in enumerate(includes) if "sync.yml" in s)
        assert stage < sync


def test_gitignore_keeps_host_local_files_out_of_gitops():
    with open(os.path.join(REPO_ROOT, "roles", "gitops", "files",
                           "gitignore.default")) as f:
        lines = f.read().splitlines()
    assert "schedule-*.log" in lines and ".schedule-state/" in lines
    assert "config/schedules.yml" not in lines


def test_delete_removes_backup_and_schedule_entries_only(tmp_path):
    """delete's crontab steps, run together against the host crontab file
    the containerized CLI hands over."""
    tasks = _tasks("roles", "delete", "tasks", "main.yml")
    names = [t.get("name") for t in tasks]
    first = names.index("Select crontab target for the backup schedule")
    last = names.index("Remove maintenance schedules")
    crontab = tmp_path / "crontab"
    other = ("5 5 * * * /usr/bin/true\n"
             "# BEGIN canasta-backup-site2\n"
             "0 2 * * * canasta backup create -i site2 --tag scheduled\n"
             "# END canasta-backup-site2\n")
    crontab.write_text(rewrite_crontab(
        other + "# BEGIN canasta-backup-site\n"
        "0 2 * * * canasta backup create -i site --tag scheduled\n"
        "# END canasta-backup-site\n", "site",
        [cron_line("a", ENTRY, "site", "/srv", "canasta")]))
    proc = _play(tmp_path, tasks[first:last + 1], {"instance_id": "site"},
                 {"CANASTA_HOST_CRONTAB": str(crontab)})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert crontab.read_text() == other


# --- schedule add refusals ---------------------------------------------------

def _add_tasks():
    tasks = _tasks("roles", "schedule", "tasks", "add.yml")
    assert tasks[0]["name"] == "Resolve instance"
    return tasks[1:]


def _fake_bin(tmp_path, name, body):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    script = bindir / name
    script.write_text("#!/bin/sh\n" + body)
    script.chmod(0o755)
    return bindir


def _add(tmp_path, inst, extra_vars=None, env_extra=None):
    play_vars = {"instance_id": "site", "instance_path": str(inst),
                 "schedule_name": "a", "cron": "* * * * *",
                 "schedule_command": "true",
                 "canasta_cli_bin": "/usr/local/bin/canasta"}
    play_vars.update(extra_vars or {})
    crontab = tmp_path / "crontab"
    crontab.touch()
    env = {"CANASTA_HOST_CRONTAB": str(crontab)}
    env.update(env_extra or {})
    return _play(tmp_path, _add_tasks(), play_vars, env)


def test_add_refuses_a_remote_canasta_without_the_schedule_command(tmp_path):
    inst = _instance(tmp_path)
    bindir = _fake_bin(tmp_path, "canasta", 'echo "invalid choice" >&2; exit 2\n')
    proc = _add(tmp_path, inst, {"target_host": "big"},
                {"PATH": "%s:%s" % (bindir, os.environ["PATH"])})
    assert proc.returncode != 0
    assert "has no 'schedule' command" in proc.stdout + proc.stderr
    assert not (inst / "config" / "schedules.yml").exists()


def test_add_refuses_a_service_that_is_not_running(tmp_path):
    inst = _instance(tmp_path)
    bindir = _fake_bin(tmp_path, "fake-docker", 'printf "web\\ndb\\n"\n')
    inspect = {"inspect_command": str(bindir / "fake-docker")}
    proc = _add(tmp_path, inst, dict(inspect, service="nosuch"))
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0
    assert "Service 'nosuch' is not running" in out and "db, web" in out
    assert not (inst / "config" / "schedules.yml").exists()

    proc = _add(tmp_path, inst, dict(inspect, service="db"))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert parse((inst / "config" / "schedules.yml").read_text())["a"][
        "service"] == "db"
    assert installed((tmp_path / "crontab").read_text(), "site") == {
        "a": ["* * * * *"]}


def test_a_skipped_run_prints_no_raw_error(tmp_path, fake_compose):
    """flock's 'lock held' exit (75), from a stand-in so it runs anywhere."""
    marker = tmp_path / "ran"
    inst = _instance(tmp_path, {"a": dict(ENTRY,
                                          command="touch %s" % marker)})
    bindir = _fake_bin(tmp_path, "flock", "exit 75\n")
    proc = _play(tmp_path, _run_tasks(),
                 {"instance_id": "site", "instance_path": str(inst),
                  "schedule_name": "a", "compose_command": str(fake_compose)},
                 dict(CLI_CALLBACK, FAKE_SERVICE_LOG=str(tmp_path / "svc"),
                      PATH="%s:%s" % (bindir, os.environ["PATH"])))
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "previous run is still in progress" in out
    assert "Error" not in out and "flock -n" not in out
    assert not marker.exists()
    assert not (inst / ".schedule-state" / "a.yml").exists()
