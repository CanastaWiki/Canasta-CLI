"""A MariaDB startup failure must leave a diagnostic somewhere findable.

The db service passes --log-error, which diverts MariaDB's log off stderr and
into the mysql-logs volume. `docker logs` therefore carried only the
entrypoint's own notes, ending on "MariaDB upgrade not required" — a line that
reads like success — while `docker inspect` gave ExitCode=1 with an empty
Error. An oversized innodb_buffer_pool_size crash-looped a container 17 times
with nothing anywhere naming the buffer pool.

This also defeated start.yml's own failure capture, which reads container
stdout precisely so an operator can see why a container was unhealthy.
"""
import os

import yaml


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
COMPOSE = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "files", "compose", "docker-compose.yml",
)
START = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks", "start.yml")


def _db_command():
    with open(COMPOSE) as fh:
        return yaml.safe_load(fh)["services"]["db"]["command"]


def _walk(tasks):
    for task in tasks or []:
        yield task
        for key in ("block", "rescue", "always"):
            for nested in _walk(task.get(key)):
                yield nested


def _named(path, name):
    with open(path) as fh:
        for task in _walk(yaml.safe_load(fh)):
            if (task.get("name") or "") == name:
                return task
    return None


class TestTheErrorLogIsTeedToStdout:
    def test_the_db_service_tails_its_error_log(self):
        assert "tail -F /var/log/mysql/error.log" in _db_command()

    def test_it_follows_by_name_so_rotation_does_not_end_it(self):
        # The command rotates error.log to error.log.1 at 10 MB; `tail -f`
        # would keep reading the rotated inode and go silent.
        cmd = _db_command()
        assert "tail -f /var/log/mysql" not in cmd
        assert "tail -F" in cmd

    def test_the_tail_starts_before_the_server_is_exec_ed(self):
        # exec replaces the shell, so anything after it never runs.
        cmd = _db_command()
        assert cmd.index("tail -F") < cmd.index("exec docker-entrypoint.sh")

    def test_the_file_is_still_written_to_the_volume(self):
        # docker logs does not survive the container recreation that
        # `canasta restart` performs; the volume-backed copy does.
        cmd = _db_command()
        assert "--log-error=/var/log/mysql/error.log" in cmd
        assert "mysql-logs:/var/log/mysql" in yaml.safe_load(
            open(COMPOSE))["services"]["db"]["volumes"]

    def test_the_chown_still_precedes_the_tail(self):
        # /var/log/mysql belongs to mysql only after the chown.
        cmd = _db_command()
        assert cmd.index("chown -R mysql:mysql") < cmd.index("tail -F")


class TestTheFailurePathReadsTheVolume:
    def test_the_error_log_is_captured_when_start_fails(self):
        task = _named(START, "Read the database error log")
        assert task, (
            "a container restarting every couple of seconds may hold nothing "
            "in stdout by the time the compose-logs capture runs"
        )
        assert task["ansible.builtin.command"]["argv"][1] == "run"
        assert task.get("failed_when") is False

    def test_the_volume_must_exist_before_it_is_mounted(self):
        # `run -v` auto-creates a missing named volume, so an
        # external-database instance would collect a stray empty one on
        # every failed start.
        check = _named(START, "Check whether the database log volume exists")
        assert check, "expected an existence check before the mount"
        assert check["ansible.builtin.command"]["argv"][1:3] == ["volume", "inspect"]
        read = _named(START, "Read the database error log")
        assert "_start_db_log_vol.rc == 0" in str(read["when"])

    def test_the_volume_name_matches_compose_project_derivation(self):
        task = _named(START, "Resolve the database log volume name")
        assert task
        expr = str(task["ansible.builtin.set_fact"]["_start_db_log_volume"])
        assert "instance_path | basename | lower" in expr
        assert "regex_replace('[^a-z0-9_-]', '')" in expr
        assert expr.rstrip().endswith("_mysql-logs")

    def test_the_captured_log_reaches_the_failure_message(self):
        msg = str(_named(START, "Fail with full output if docker compose up failed")
                  ["ansible.builtin.fail"]["msg"])
        assert "_start_db_log.stdout" in msg
        # Skipped leaves a dict with no rc, so a bare .rc raises.
        assert "_start_db_log.rc | default(1)" in msg

    def test_it_is_read_read_only(self):
        argv = _named(START, "Read the database error log")["ansible.builtin.command"]["argv"]
        assert any(str(a).endswith(":/l:ro") for a in argv)
