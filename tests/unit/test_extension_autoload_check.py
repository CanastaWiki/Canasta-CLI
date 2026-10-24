"""`extension|skin add` must not leave an autoloader that requires missing files.

Composer writes every autoload.files entry of a registered composer.json into
vendor/composer/autoload_files.php whether or not the file exists, and every
wiki then fails to load. add checks the entries before registering, checks the
autoloader after composer runs, rolls back the registration, and fails.
"""

import json
import os
import re
import shutil
import subprocess

import jinja2
import pytest
import yaml

import canasta_composer_autoload
from mock_ansible import run_module_with_params

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
ROLE = os.path.join(REPO_ROOT, "roles", "extensions_skins")
ADD = os.path.join(ROLE, "tasks", "add.yml")
PHP_CHECK = os.path.join(ROLE, "files", "missing_autoload_files.php")


def _add_tasks():
    with open(ADD) as f:
        return yaml.safe_load(f)


def _task(name):
    matches = [t for t in _add_tasks() if t.get("name") == name]
    assert len(matches) == 1, name
    return matches[0]


def _index(name):
    return [t.get("name") for t in _add_tasks()].index(name)


def _env():
    env = jinja2.Environment(undefined=jinja2.StrictUndefined)
    env.tests["match"] = lambda value, pattern: bool(re.match(pattern, value))
    return env


def _render(expr, **context):
    return _env().from_string(expr).render(**context)


def _extension(instance, name, composer, files=()):
    d = os.path.join(instance, "extensions", name)
    os.makedirs(d)
    with open(os.path.join(d, "composer.json"), "w") as f:
        f.write(composer if isinstance(composer, str) else json.dumps(composer))
    for path in files:
        full = os.path.join(d, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        open(full, "w").close()
    return "extensions/%s/composer.json" % name


def _check(instance, include):
    result, failed, _ = run_module_with_params(
        canasta_composer_autoload,
        {"instance_path": instance, "include": include})
    assert failed is False
    assert result["changed"] is False
    return result["broken"]


class TestModule:
    def test_reports_missing_files(self, tmp_dir):
        inc = _extension(tmp_dir, "Foo", {"autoload": {"files": [
            "Foo.php", "src/helpers.php", "Gone.php"]}},
            files=["Foo.php", "src/helpers.php"])
        assert _check(tmp_dir, [inc]) == [
            {"include": inc, "missing": ["Gone.php"]}]

    def test_all_present_is_not_reported(self, tmp_dir):
        inc = _extension(tmp_dir, "Foo", {"autoload": {"files": ["Foo.php"]}},
                         files=["Foo.php"])
        assert _check(tmp_dir, [inc]) == []

    def test_only_broken_items_are_reported(self, tmp_dir):
        good = _extension(tmp_dir, "Good", {"require": {"a/b": "^1"}})
        bad = _extension(tmp_dir, "Bad", {"autoload": {"files": ["Bad.php"]}})
        assert _check(tmp_dir, [good, bad]) == [
            {"include": bad, "missing": ["Bad.php"]}]

    def test_directory_is_not_a_file(self, tmp_dir):
        inc = _extension(tmp_dir, "Foo", {"autoload": {"files": ["src"]}},
                         files=["src/x.php"])
        assert _check(tmp_dir, [inc]) == [
            {"include": inc, "missing": ["src"]}]

    @pytest.mark.parametrize("composer", [
        "{not json",
        "[]",
        json.dumps({"autoload": []}),
        json.dumps({"autoload": {"files": "Foo.php"}}),
        json.dumps({"autoload": {"psr-4": {"Foo\\": "src/"}}}),
        json.dumps({"autoload-dev": {"files": ["Gone.php"]}}),
    ])
    def test_nothing_to_check(self, tmp_dir, composer):
        inc = _extension(tmp_dir, "Foo", composer)
        assert _check(tmp_dir, [inc]) == []

    def test_missing_composer_json_is_not_reported(self, tmp_dir):
        assert _check(tmp_dir, ["extensions/Absent/composer.json"]) == []


@pytest.mark.skipif(shutil.which("php") is None, reason="php not installed")
class TestAutoloaderCheck:
    def _run(self, mw_home, entries):
        composer = os.path.join(mw_home, "vendor", "composer")
        os.makedirs(composer, exist_ok=True)
        with open(os.path.join(composer, "autoload_files.php"), "w") as f:
            f.write("<?php\n$vendorDir = dirname(__DIR__);\n"
                    "$baseDir = dirname($vendorDir);\nreturn array(\n")
            for i, entry in enumerate(entries):
                f.write("    'h%d' => %s,\n" % (i, entry))
            f.write(");\n")
        with open(PHP_CHECK) as script:
            return subprocess.run(
                ["php", "--"], stdin=script, capture_output=True, text=True,
                env={**os.environ, "MW_HOME": mw_home}, timeout=30)

    def test_reports_missing_relative_to_core(self, tmp_dir):
        os.makedirs(os.path.join(tmp_dir, "extensions", "Ok"))
        open(os.path.join(tmp_dir, "extensions", "Ok", "Ok.php"), "w").close()
        result = self._run(tmp_dir, [
            "$vendorDir . '/../extensions/Ok/Ok.php'",
            "$vendorDir . '/composer/../../extensions/Gone/Gone.php'"])
        assert result.returncode == 1, result.stderr
        assert result.stdout.splitlines() == ["extensions/Gone/Gone.php"]

    def test_all_present(self, tmp_dir):
        open(os.path.join(tmp_dir, "a.php"), "w").close()
        result = self._run(tmp_dir, ["$baseDir . '/a.php'"])
        assert result.returncode == 0, result.stderr
        assert result.stdout == ""

    def test_no_autoload_files_list(self, tmp_dir):
        with open(PHP_CHECK) as script:
            result = subprocess.run(
                ["php", "--"], stdin=script, capture_output=True, text=True,
                env={**os.environ, "MW_HOME": tmp_dir}, timeout=30)
        assert result.returncode == 0, result.stderr
        assert result.stdout == ""


class TestAddFlow:
    def test_checked_before_registering(self):
        assert (_index("Check the autoload files of added {{ _item_type }}")
                < _index("Leave out {{ _item_type }} that autoload missing files")
                < _index("Register composer requirements in composer.local.json"))

    def test_broken_items_are_left_out(self):
        leave_out = _task(
            "Leave out {{ _item_type }} that autoload missing files")
        facts = leave_out["ansible.builtin.set_fact"]
        env = _env()
        env.tests["in"] = lambda value, seq: value in seq
        rendered = env.from_string(facts["_composer_paths"]).render(
            _composer_paths=["extensions/A/composer.json",
                             "extensions/B/composer.json",
                             "extensions/C/composer.json"],
            _autoload_check={"broken": [
                {"include": "extensions/B/composer.json",
                 "missing": ["B.php"]}]})
        assert rendered.strip() == (
            "['extensions/A/composer.json', 'extensions/C/composer.json']")

    def test_autoloader_checked_after_a_successful_install(self):
        check = _task("Check that the files the autoloader requires exist")
        assert (_index("Install composer requirements (--no-dev)")
                < _index("Record the composer install result")
                < _index("Check that the files the autoloader requires exist")
                < _index("Record the files the autoloader requires that do "
                         "not exist")
                < _index("Commit composer.local.json (gitops) on successful "
                         "install"))
        assert ("_composer_install_result.rc | default(1) == 0"
                in check["when"])
        assert check["vars"]["exec_stdin"].endswith(
            "/roles/extensions_skins/files/missing_autoload_files.php")
        assert check["ansible.builtin.include_tasks"]["apply"] == {
            "ignore_errors": True}

    @pytest.mark.parametrize("install_rc,check,expected", [
        (0, {"rc": 1, "stdout_lines": ["extensions/X/X.php"]},
         ["extensions/X/X.php"]),
        (0, {"rc": 0, "stdout_lines": []}, []),
        (0, {"rc": 255, "stdout_lines": ["PHP Parse error"]}, []),
        (2, {"rc": 1, "stdout_lines": ["composer noise"]}, []),
    ])
    def test_missing_recorded_only_from_the_check(self, install_rc, check,
                                                 expected):
        record = _task(
            "Record the files the autoloader requires that do not exist")
        env = jinja2.Environment(undefined=jinja2.ChainableUndefined)
        rendered = env.from_string(
            record["ansible.builtin.set_fact"]["_autoload_missing"]).render(
            _composer_paths=["extensions/X/composer.json"],
            _composer_install_result={"rc": install_rc}, exec_result=check)
        assert rendered.strip() == str(expected)

    def test_missing_files_block_the_commit_and_revert(self):
        commit = _task(
            "Commit composer.local.json (gitops) on successful install")
        assert "_autoload_missing | length == 0" in commit["when"]
        assert "exec_result" not in str(commit["when"])
        revert = _task("Revert composer registration when install failed")
        assert any("_autoload_missing | length > 0" in w
                   for w in revert["when"])
        warn = [t for t in revert["block"]
                if t["name"] == "Warn that the composer install failed"][0]
        assert warn["when"] == "_composer_install_result.rc | default(1) != 0"

    def test_fails_after_the_revert_and_before_enabling(self):
        assert (_index("Revert composer registration when install failed")
                < _index("Fail: {{ _item_type }} autoload files that do not "
                         "exist")
                < _index("Enable added {{ _item_type }} (with containers up "
                         "so the monitor has linked them)"))


class TestFailMessage:
    def _message(self, **context):
        fail = _task("Fail: {{ _item_type }} autoload files that do not exist")
        context.setdefault("_item_type", "extensions")
        context.setdefault("_item_dir", context["_item_type"])
        context.setdefault("_autoload_broken", [])
        context.setdefault("_autoload_missing", [])
        context.setdefault("exec_result", {"rc": 0})
        context.setdefault("skip_enable", False)
        context.setdefault("_composer_local", {"added": []})
        env = jinja2.Environment(undefined=jinja2.ChainableUndefined)
        env.filters["bool"] = bool
        env.filters["dirname"] = os.path.dirname
        env.filters["basename"] = os.path.basename
        env.filters["regex_replace"] = lambda v, p, r: re.sub(p, r, v)
        env.filters["unique"] = lambda v: list(dict.fromkeys(v))
        env.tests["match"] = lambda v, p: re.match(p, v) is not None
        context.setdefault("_to_add", [{"name": "Foo"}])
        for key in ("_autoload_left_out", "_autoload_owners",
                    "_autoload_names"):
            context[key] = yaml.safe_load(env.from_string(
                fail["vars"][key]).render(**context))
        rendered = env.from_string(
            fail["ansible.builtin.fail"]["msg"]).render(**context)
        assert "  " not in rendered
        return rendered

    def test_broken_before_registering(self):
        msg = self._message(_autoload_broken=[
            {"include": "extensions/Foo/composer.json",
             "missing": ["Foo.php"]}], skip_enable=True)
        assert msg == (
            "extensions/Foo/composer.json autoloads files that are not in the "
            "repository: Foo.php. So that the wikis keep loading, "
            "extensions/Foo/composer.json was left out of "
            "config/composer.local.json. The extension code was added. Fix "
            "the autoload.files entries of the composer.json and re-run the "
            "add, or remove the extension with 'canasta extension remove "
            "Foo'.")

    def test_missing_after_composer(self):
        msg = self._message(
            _autoload_missing=["extensions/Foo/Foo.php"],
            _composer_local={"added": ["extensions/Foo/composer.json",
                                       "extensions/Bar/composer.json"]},
            exec_result={"rc": 3}, _item_type="skins")
        assert msg.startswith(
            "After composer update the autoloader requires files that do not "
            "exist: extensions/Foo/Foo.php. So that the wikis keep loading, "
            "extensions/Foo/composer.json, extensions/Bar/composer.json were "
            "left out of config/composer.local.json. The skin code was added "
            "but nothing was enabled.")
        assert "'canasta skin remove Foo,Bar'" in msg
        assert msg.endswith(
            "Rebuilding composer state from the restored list failed (exit "
            "3); run 'canasta restart' to rebuild it.")

    def test_nothing_rolled_back(self):
        msg = self._message(_autoload_missing=["extensions/Foo/Foo.php"])
        assert "left out" not in msg
        assert "Foo.php. The extension code was added" in msg
        assert "'canasta extension remove Foo'" in msg

    def test_names_the_registered_item_whose_file_is_missing(self):
        msg = self._message(
            _autoload_missing=["extensions/Bad/Bad.php"],
            _composer_local={"added": ["extensions/Good/composer.json"]},
            _to_add=[{"name": "Good"}])
        assert "'canasta extension remove Bad'" in msg
