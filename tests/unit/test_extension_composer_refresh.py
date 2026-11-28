"""The in-container composer refresh run by `extension|skin add|remove`.

The image's /update-composer-deps.sh keeps the bundled include list itself,
so config/composer.local.json is never copied over the effective file; the
script runs, and a composer-update.failed file it leaves counts as a
failure. add, its revert, and remove all run it through _composer_update.yml.

The rendered commands run under bash with the container paths redirected
into a temporary directory and `composer` stubbed.
"""

import os
import re
import shlex
import stat
import subprocess

import jinja2
import pytest
import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
TASKS = os.path.join(REPO_ROOT, "roles", "extensions_skins", "tasks")
ADD = os.path.join(TASKS, "_add_composer.yml")
REMOVE = os.path.join(TASKS, "remove.yml")
SHARED = os.path.join(TASKS, "_composer_update.yml")

SCRIPT = "/update-composer-deps.sh"
FAILED = "/mediawiki/config/persistent/composer-update.failed"


def _walk(tasks):
    for t in tasks or []:
        if not isinstance(t, dict):
            continue
        yield t
        for nested in ("block", "rescue", "always"):
            if nested in t:
                yield from _walk(t[nested])


def _tasks(path):
    with open(path) as f:
        return list(_walk(yaml.safe_load(f)))


def _refresh_command():
    cmds = [(t.get("vars") or {}).get("exec_command", "")
            for t in _tasks(SHARED)]
    cmds = [c for c in cmds if c]
    assert len(cmds) == 1
    return cmds[0]


def _includes_shared(task):
    return (task.get("ansible.builtin.include_tasks", "")
            .endswith("/roles/extensions_skins/tasks/_composer_update.yml"))


def _warning_reason_expr(path):
    exprs = [(t.get("vars") or {}).get("_composer_failed_reason")
             for t in _tasks(path)
             if "ansible.builtin.debug" in t]
    exprs = [e for e in exprs if e]
    assert len(exprs) == 1
    return exprs[0]


def _env():
    env = jinja2.Environment(undefined=jinja2.ChainableUndefined)
    env.filters["quote"] = shlex.quote
    env.tests["match"] = lambda value, pattern: bool(re.match(pattern, value))
    env.filters["regex_replace"] = (
        lambda value, pattern, repl: re.sub(pattern, repl, value))
    return env


class _Container:
    """Container paths mapped under a temporary root."""

    def __init__(self, root, image_script=None):
        self.root = root
        self.mw = root / "var/www/mediawiki/w"
        self.config = root / "mediawiki/config"
        (self.config / "persistent").mkdir(parents=True)
        self.mw.mkdir(parents=True)
        (self.config / "composer.local.json").write_text('{"operator": true}')
        (self.mw / "composer.local.json").write_text('{"effective": true}')
        bin_dir = root / "bin"
        bin_dir.mkdir()
        self._executable(bin_dir / "composer",
                         f'echo "composer $*" >> {root}/calls\n')
        self.script = root / "update-composer-deps.sh"
        if image_script is not None:
            self._executable(self.script, image_script)
        self.path = f"{bin_dir}:{os.environ['PATH']}"

    @staticmethod
    def _executable(path, body):
        path.write_text("#!/bin/bash\n" + body)
        path.chmod(path.stat().st_mode | stat.S_IEXEC)

    def run(self, command):
        command = (command
                   .replace(SCRIPT, str(self.script))
                   .replace("/mediawiki/config", str(self.config))
                   .replace("/var/www/mediawiki/w", str(self.mw)))
        return subprocess.run(
            ["bash", "-c", command], capture_output=True, text=True,
            env={**os.environ, "PATH": self.path}, timeout=30)

    def calls(self):
        calls = self.root / "calls"
        return calls.read_text().splitlines() if calls.exists() else []


def _render(**context):
    return _env().from_string(_refresh_command()).render(**context)


@pytest.mark.parametrize("path, name, waits", [
    (ADD, "Install composer requirements (--no-dev)", True),
    (REMOVE, "Install composer state (--no-dev)", False),
])
def test_add_and_remove_run_the_shared_refresh(path, name, waits):
    task = [t for t in _tasks(path) if t.get("name") == name]
    assert len(task) == 1 and _includes_shared(task[0])
    wait = (task[0].get("vars") or {}).get("composer_update_wait")
    assert wait == ("{{ _composer_paths }}" if waits else None)


def test_the_refresh_is_best_effort():
    task = [t for t in _tasks(SHARED) if (t.get("vars") or {}).get("exec_command")]
    assert task[0]["vars"]["rx_fail"] is False
    assert task[0]["vars"]["exec_long"] is True


@pytest.fixture(params=["waiting", "direct"])
def command(request, tmp_path):
    def make(container):
        if request.param == "waiting":
            (container.mw / "extensions/Foo").mkdir(parents=True)
            (container.mw / "extensions/Foo/composer.json").write_text("{}")
            return _render(composer_update_wait=["extensions/Foo/composer.json"])
        return _render()
    return make


class TestImageScript:
    def test_runs_script_without_copying_or_composer(self, tmp_path, command):
        c = _Container(tmp_path, f'echo script >> {tmp_path}/calls\n')
        result = c.run(command(c))
        assert result.returncode == 0, result.stderr
        assert c.calls() == ["script"]
        assert (c.mw / "composer.local.json").read_text() == (
            '{"effective": true}')

    def test_script_exit_code_is_the_result(self, tmp_path, command):
        c = _Container(tmp_path, "exit 4\n")
        assert c.run(command(c)).returncode == 4

    def test_failed_file_counts_as_failure(self, tmp_path, command):
        c = _Container(tmp_path, (
            f'printf "Composer update failed at T (exit 2).\\nmore\\n" '
            f'> {tmp_path}/mediawiki/config/persistent/'
            f'composer-update.failed\n'))
        result = c.run(command(c))
        assert result.returncode == 1
        assert ("composer-update.failed: Composer update failed at T "
                "(exit 2).") in result.stderr.splitlines()
        assert "more" not in result.stderr


class TestImageWithoutTheScript:
    def test_fails_without_touching_composer(self, tmp_path, command):
        c = _Container(tmp_path)
        result = c.run(command(c))
        assert result.returncode == 1
        assert "upgrade the instance to a current Canasta image" in result.stderr
        assert c.calls() == []
        assert (c.mw / "composer.local.json").read_text() == (
            '{"effective": true}')


class TestAddWaitsBeforeTheRefresh:
    def test_missing_include_fails_before_the_script(self, tmp_path):
        c = _Container(tmp_path, f'echo script >> {tmp_path}/calls\n')
        cmd = _render(composer_update_wait=["extensions/Gone/composer.json"])
        # Skip the 30-second wait.
        cmd = cmd.replace("[ $i -lt 30 ]", "[ $i -lt 0 ]")
        result = c.run(cmd)
        assert result.returncode == 1
        assert "is not visible in the container" in result.stderr
        assert c.calls() == []


@pytest.mark.parametrize("path", [ADD, REMOVE])
class TestWarningReason:
    def _reason(self, path, stderr):
        # add reads the install result saved before its rebuild.
        result = {"stderr": stderr}
        return _env().from_string(_warning_reason_expr(path)).render(
            exec_result=result, _composer_install_result=result).strip()

    def test_surfaces_the_failed_file_line(self, path):
        stderr = ("composer noise\n"
                  "composer-update.failed: Composer update failed at T.\n")
        # The line's own period would double the sentence's.
        assert self._reason(path, stderr) == (
            "; config/persistent/composer-update.failed: "
            "Composer update failed at T")

    def test_empty_without_the_marker(self, path):
        assert self._reason(path, "composer noise\n") == ""

    def test_reason_is_in_the_message(self, path):
        msgs = [t["ansible.builtin.debug"]["msg"] for t in _tasks(path)
                if (t.get("vars") or {}).get("_composer_failed_reason")]
        assert msgs and all("{{ _composer_failed_reason }}" in m
                            for m in msgs)


def _revert_block():
    with open(ADD) as f:
        tasks = yaml.safe_load(f)
    names = [t.get("name", "") for t in tasks]
    i = names.index("Revert composer registration when install failed")
    return tasks, i, tasks[i]


def _rebuild_task():
    rebuild = [t for t in _revert_block()[2]["block"]
               if t.get("name") == "Rebuild composer state from the restored list"]
    assert len(rebuild) == 1
    return rebuild[0]


class TestAddRebuildAfterRevert:
    def test_rebuild_follows_unregister_and_precedes_warning(self):
        names = [t["name"] for t in _revert_block()[2]["block"]]
        assert names == [
            "Unregister composer requirements to keep config clean",
            "Rebuild composer state from the restored list",
            "Warn that the composer install failed",
        ]

    def test_block_gates_on_the_saved_install_result(self):
        # The rebuild overwrites exec_result and block conditions are
        # re-evaluated per task, so the gate must not read exec_result.
        tasks, i, block = _revert_block()
        assert any("_composer_install_result.rc | default(1) != 0" in w
                   for w in block["when"])
        assert "exec_result" not in str(block["when"])
        # Recorded straight after the install, before the autoload check
        # overwrites exec_result.
        names = [t.get("name", "") for t in tasks]
        install = names.index("Install composer requirements (--no-dev)")
        record = tasks[install + 1]
        assert record["ansible.builtin.set_fact"] == {
            "_composer_install_result": "{{ exec_result }}"}
        assert install + 1 < i

    def test_rebuild_runs_the_shared_refresh_without_waiting(self):
        rebuild = _rebuild_task()
        assert _includes_shared(rebuild)
        assert "composer_update_wait" not in (rebuild.get("vars") or {})


class TestAddWarningMessage:
    def _message(self, install, rebuild):
        warn = [t for t in _revert_block()[2]["block"]
                if t.get("name") == "Warn that the composer install failed"][0]
        env = _env()
        context = {"_to_add": [{"name": "Foo"}], "_item_type": "extensions",
                   "_composer_install_result": install,
                   "exec_result": rebuild}
        for key in ("_composer_failed_reason", "_composer_rebuild_reason",
                    "_composer_rebuild_failure"):
            context[key] = env.from_string(warn["vars"][key]).render(
                **context)
        return env.from_string(warn["ansible.builtin.debug"]["msg"]).render(
            **context)

    def test_successful_rebuild_adds_nothing(self):
        msg = self._message(
            {"rc": 2, "stderr": "composer-update.failed: first (exit 2).\n"},
            {"rc": 0, "stderr": ""})
        assert ("(composer exited 2); config/persistent/composer-update"
                ".failed: first (exit 2). The extensions code") in msg
        assert "Rebuilding" not in msg
        assert msg.endswith("register the requirements manually.")

    def test_failed_rebuild_is_appended(self):
        msg = self._message(
            {"rc": 2, "stderr": "composer-update.failed: first (exit 2).\n"},
            {"rc": 3, "stderr": "composer-update.failed: second (exit 3).\n"})
        assert msg.endswith(
            "manually. Rebuilding composer state from the restored list "
            "also failed (exit 3); config/persistent/composer-update.failed: "
            "second (exit 3).")
        assert ".)." not in msg


class TestRemoveWarningMessage:
    def test_reads_cleanly(self):
        warn = [t for t in _tasks(REMOVE)
                if (t.get("vars") or {}).get("_composer_failed_reason")][0]
        env = _env()
        context = {"_item_type": "extensions",
                   "exec_result": {"rc": 2, "stderr":
                                   "composer-update.failed: first (exit 2).\n"}}
        context["_composer_failed_reason"] = env.from_string(
            warn["vars"]["_composer_failed_reason"]).render(**context)
        msg = env.from_string(warn["ansible.builtin.debug"]["msg"]).render(
            **context)
        assert ("(composer exited 2); config/persistent/composer-update"
                ".failed: first (exit 2). The extensions code") in msg
