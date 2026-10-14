"""The in-container composer refresh run by `extension|skin add|remove`.

An image with /update-composer-deps.sh keeps the bundled include list itself,
so config/composer.local.json must not be copied over the effective file;
the script runs instead, and a composer-update.failed file it leaves counts
as a failure. Older images keep the copy + `composer update`.

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
ADD = os.path.join(TASKS, "add.yml")
REMOVE = os.path.join(TASKS, "remove.yml")

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


def _refresh_command(path):
    cmds = [(t.get("vars") or {}).get("exec_command", "")
            for t in _tasks(path)]
    cmds = [c for c in cmds if "composer update" in c]
    assert len(cmds) == 1
    return cmds[0]


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


def _render(path, **context):
    return _env().from_string(_refresh_command(path)).render(**context)


@pytest.fixture(params=["add", "remove"])
def command(request, tmp_path):
    def make(container):
        if request.param == "add":
            (container.mw / "extensions/Foo").mkdir(parents=True)
            (container.mw / "extensions/Foo/composer.json").write_text("{}")
            return _render(ADD, _composer_paths=["extensions/Foo/composer.json"])
        return _render(REMOVE)
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


class TestOlderImage:
    def test_copies_and_runs_composer_update(self, tmp_path, command):
        c = _Container(tmp_path)
        result = c.run(command(c))
        assert result.returncode == 0, result.stderr
        assert c.calls() == ["composer update --no-dev --no-interaction"]
        assert (c.mw / "composer.local.json").read_text() == (
            '{"operator": true}')

    def test_composer_failure_is_the_result(self, tmp_path, command):
        c = _Container(tmp_path)
        (tmp_path / "bin/composer").write_text("#!/bin/bash\nexit 2\n")
        assert c.run(command(c)).returncode == 2


class TestAddWaitsBeforeTheRefresh:
    def test_missing_include_fails_before_the_script(self, tmp_path):
        c = _Container(tmp_path, f'echo script >> {tmp_path}/calls\n')
        cmd = _render(ADD, _composer_paths=["extensions/Gone/composer.json"])
        # Skip the 30-second wait.
        cmd = cmd.replace("[ $i -lt 30 ]", "[ $i -lt 0 ]")
        result = c.run(cmd)
        assert result.returncode == 1
        assert "is not visible in the container" in result.stderr
        assert c.calls() == []


@pytest.mark.parametrize("path", [ADD, REMOVE])
class TestWarningReason:
    def _reason(self, path, stderr):
        return _env().from_string(_warning_reason_expr(path)).render(
            exec_result={"stderr": stderr}).strip()

    def test_surfaces_the_failed_file_line(self, path):
        stderr = ("composer noise\n"
                  "composer-update.failed: Composer update failed at T.\n")
        assert self._reason(path, stderr) == (
            "; config/persistent/composer-update.failed: "
            "Composer update failed at T.")

    def test_empty_without_the_marker(self, path):
        assert self._reason(path, "composer noise\n") == ""

    def test_reason_is_in_the_message(self, path):
        msgs = [t["ansible.builtin.debug"]["msg"] for t in _tasks(path)
                if (t.get("vars") or {}).get("_composer_failed_reason")]
        assert msgs and all("{{ _composer_failed_reason }}" in m
                            for m in msgs)
