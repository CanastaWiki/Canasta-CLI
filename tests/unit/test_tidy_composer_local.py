"""upgrade tidies config/composer.local.json for images that keep their own
bundled composer list.

The classifier runs the image's composer-local.php; fixtures/composer-local.php
is a verbatim copy of CanastaBase's _sources/scripts/composer-local.php, so
these tests exercise the image's rules against a MediaWiki layout of real
directories and symlinks in tmp. The upgrade task file runs as a localhost
playbook with exec.yml replaced by a local stub.
"""

import base64
import json
import os
import shutil
import subprocess
import sys

import pytest
import yaml

import canasta_composer_local
from mock_ansible import run_module_with_params

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "composer-local.php")
CLASSIFIER = os.path.join(REPO_ROOT, "roles", "upgrade", "files",
                          "classify_composer_includes.php")
TIDY = os.path.join(REPO_ROOT, "roles", "upgrade", "tasks", "tidy_composer_local.yml")
MAIN = os.path.join(REPO_ROOT, "roles", "upgrade", "tasks", "main.yml")

PHP = shutil.which("php")
needs_php = pytest.mark.skipif(PHP is None, reason="php is not installed")

BUNDLED = "extensions/SemanticMediaWiki/composer.json"
BUNDLED_SKIN = "skins/Chameleon/composer.json"
OVERRIDDEN = "extensions/Maps/composer.json"
USER = "extensions/Mine/composer.json"
USER_SKIN = "skins/MySkin/composer.json"
MISSING = "extensions/Gone/composer.json"


def _composer_json(path):
    os.makedirs(path)
    with open(os.path.join(path, "composer.json"), "w") as f:
        f.write('{"require": {}}\n')


def _mediawiki(root, new_image=True):
    """A MediaWiki tree laid out like the image's, with extensions/ and
    skins/ symlinking into the bundled and user directories."""
    mw = os.path.join(root, "mw")
    for kind, bundled, user in (
            ("extensions", ["SemanticMediaWiki", "Maps"], ["Mine", "Maps"]),
            ("skins", ["Chameleon"], ["MySkin"])):
        os.makedirs(os.path.join(mw, kind))
        for name in bundled:
            _composer_json(os.path.join(mw, "canasta-" + kind, name))
        for name in user:
            _composer_json(os.path.join(mw, "user-" + kind, name))
        for name in bundled:
            os.symlink(os.path.join("..", "canasta-" + kind, name),
                       os.path.join(mw, kind, name))
        for name in user:
            link = os.path.join(mw, kind, name)
            if os.path.islink(link):
                os.unlink(link)
            os.symlink(os.path.join("..", "user-" + kind, name), link)
    os.makedirs(os.path.join(mw, "maintenance"))
    shutil.copy(FIXTURE, os.path.join(mw, "maintenance", "composer-local.php"))
    if new_image:
        with open(os.path.join(mw, "composer.bundled.json"), "w") as f:
            json.dump({"extra": {"merge-plugin": {"include": [
                BUNDLED, BUNDLED_SKIN, OVERRIDDEN]}}}, f)
    outside = os.path.join(root, "outside", "pkg")
    _composer_json(outside)
    return mw, os.path.join(outside, "composer.json")


def _classify(mw, includes):
    arg = base64.b64encode(json.dumps(includes).encode()).decode()
    with open(CLASSIFIER) as f:
        proc = subprocess.run([PHP, "--", arg], stdin=f, capture_output=True,
                              text=True, env=dict(os.environ, MW_HOME=mw))
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.splitlines()[-1])


@needs_php
class TestClassifier:
    def test_drops_bundled_and_missing_keeps_operator(self, tmp_path):
        mw, outside = _mediawiki(str(tmp_path))
        includes = [BUNDLED, BUNDLED_SKIN, OVERRIDDEN, USER, USER_SKIN,
                    MISSING, outside]
        result = _classify(mw, includes)
        assert result == {"supported": True,
                          "drop": [BUNDLED, BUNDLED_SKIN, MISSING]}

    def test_glob_kept_when_any_match_is_the_operators(self, tmp_path):
        mw, _ = _mediawiki(str(tmp_path))
        result = _classify(mw, ["extensions/*/composer.json",
                                "skins/Chameleon/*.json"])
        assert result["drop"] == ["skins/Chameleon/*.json"]

    def test_old_image_is_unsupported(self, tmp_path):
        mw, _ = _mediawiki(str(tmp_path), new_image=False)
        assert _classify(mw, [BUNDLED]) == {"supported": False}

    def test_leaves_no_temp_files(self, tmp_path):
        mw, _ = _mediawiki(str(tmp_path))
        tmp = tmp_path / "phptmp"
        tmp.mkdir()
        arg = base64.b64encode(json.dumps([BUNDLED]).encode())
        with open(CLASSIFIER) as f:
            subprocess.run([PHP, "--", arg.decode()], stdin=f, check=True,
                           capture_output=True,
                           env=dict(os.environ, MW_HOME=mw, TMPDIR=str(tmp)))
        assert list(tmp.iterdir()) == []


def _instance(root, content):
    inst = os.path.join(root, "inst")
    os.makedirs(os.path.join(inst, "config"))
    with open(os.path.join(inst, "config", "composer.local.json"), "w") as f:
        f.write(content if isinstance(content, str)
                else json.dumps(content, indent=4) + "\n")
    return inst


def _read(inst):
    with open(os.path.join(inst, "config", "composer.local.json")) as f:
        return json.load(f)


class TestPruneModule:
    def _prune(self, inst, include):
        return run_module_with_params(
            canasta_composer_local,
            {"instance_path": inst, "include": include, "state": "prune"})

    def test_keeps_other_keys(self, tmp_path):
        inst = _instance(str(tmp_path), {
            "require": {"foo/bar": "^1"},
            "minimum-stability": "dev",
            "extra": {"canasta": {"exclude-bundled": [BUNDLED_SKIN]},
                      "merge-plugin": {"include": [BUNDLED, USER],
                                       "merge-dev": False}}})
        result, failed, _ = self._prune(inst, [BUNDLED])
        assert not failed
        assert result["changed"] and not result["deleted"]
        assert result["removed"] == [BUNDLED]
        assert _read(inst) == {
            "require": {"foo/bar": "^1"},
            "minimum-stability": "dev",
            "extra": {"canasta": {"exclude-bundled": [BUNDLED_SKIN]},
                      "merge-plugin": {"include": [USER], "merge-dev": False}}}

    def test_emptied_include_list_is_dropped(self, tmp_path):
        inst = _instance(str(tmp_path), {
            "require": {"foo/bar": "^1"},
            "extra": {"merge-plugin": {"include": [BUNDLED]}}})
        result, failed, _ = self._prune(inst, [BUNDLED])
        assert not failed and not result["deleted"]
        assert _read(inst) == {"require": {"foo/bar": "^1"}}

    def test_names_removed_entries(self, tmp_path):
        odd = "vendor-extra/composer.json"
        glob = "extensions/*/composer.json"
        inst = _instance(str(tmp_path), {"extra": {"merge-plugin": {
            "include": [BUNDLED, BUNDLED_SKIN, odd, glob, USER]}}})
        result, failed, _ = self._prune(inst, [BUNDLED, BUNDLED_SKIN, odd, glob])
        assert not failed
        assert result["removed_names"] == [
            "SemanticMediaWiki", "Chameleon (skin)", odd, glob]

    def test_deletes_file_with_nothing_left(self, tmp_path):
        inst = _instance(str(tmp_path), {
            "require": {}, "repositories": [],
            "extra": {"merge-plugin": {"include": [BUNDLED, MISSING]}}})
        result, failed, _ = self._prune(inst, [BUNDLED, MISSING])
        assert not failed and result["deleted"]
        assert not os.path.exists(
            os.path.join(inst, "config", "composer.local.json"))

    def test_unchanged_file_is_not_deleted(self, tmp_path):
        inst = _instance(str(tmp_path), {})
        result, failed, _ = self._prune(inst, [BUNDLED])
        assert not failed
        assert not result["changed"] and not result["deleted"]
        assert os.path.exists(os.path.join(inst, "config", "composer.local.json"))

    def test_query_reports_parse_error_without_failing(self, tmp_path):
        inst = _instance(str(tmp_path), "{not json")
        result, failed, _ = run_module_with_params(
            canasta_composer_local, {"instance_path": inst, "state": "query"})
        assert not failed
        assert "Cannot parse" in result["error"]
        assert result["include"] == []

    def test_query_lists_string_includes(self, tmp_path):
        inst = _instance(str(tmp_path), {
            "extra": {"merge-plugin": {"include": [BUNDLED, 3, USER]}}})
        result, failed, _ = run_module_with_params(
            canasta_composer_local, {"instance_path": inst, "state": "query"})
        assert not failed and result["error"] is None
        assert result["include"] == [BUNDLED, USER]


STUB_EXEC = """\
- name: Run the command locally
  ansible.builtin.shell:
    cmd: "{{ exec_command }}"
    stdin: "{{ lookup('file', exec_stdin) }}"
  environment:
    MW_HOME: "{{ test_mw_home }}"
  register: exec_result
  changed_when: false
"""


def _canasta_root(tmp_path):
    root = tmp_path / "canasta"
    if root.exists():
        return str(root)
    (root / "roles" / "orchestrator" / "tasks").mkdir(parents=True)
    (root / "roles" / "orchestrator" / "tasks" / "exec.yml").write_text(STUB_EXEC)
    (root / "roles" / "upgrade").mkdir()
    os.symlink(os.path.join(REPO_ROOT, "roles", "upgrade", "files"),
               root / "roles" / "upgrade" / "files")
    (root / "roles" / "gitops").mkdir()
    os.symlink(os.path.join(REPO_ROOT, "roles", "gitops", "tasks"),
               root / "roles" / "gitops" / "tasks")
    return str(root)


def _run_tidy(tmp_path, inst, mw, running=True):
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {"instance_path": inst, "instance_id": "demo",
                 "instance_orchestrator": "compose", "_was_running": running,
                 "canasta_root": _canasta_root(tmp_path), "test_mw_home": mw,
                 "ansible_python_interpreter": sys.executable},
        "tasks": [{"ansible.builtin.include_tasks": TIDY}],
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_LIBRARY=os.path.join(REPO_ROOT, "roles", "extensions_skins",
                                            "library"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env, cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc.stdout


def _git(inst, *args):
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=inst, check=True, capture_output=True, text=True).stdout


OLD_COPY = {"extra": {"merge-plugin": {"include": [
    BUNDLED, BUNDLED_SKIN, OVERRIDDEN, MISSING, USER]}}}


@needs_php
class TestUpgradeTidy:
    def test_removes_bundled_and_reports(self, tmp_path):
        mw, _ = _mediawiki(str(tmp_path))
        inst = _instance(str(tmp_path), OLD_COPY)
        out = _run_tidy(tmp_path, inst, mw)
        assert _read(inst) == {"extra": {"merge-plugin": {"include": [
            OVERRIDDEN, USER]}}}
        assert ("Removed 3 entries from config/composer.local.json of 'demo' "
                "that the image already provides or that match nothing: "
                "SemanticMediaWiki, Chameleon (skin), Gone.") in out
        # A second upgrade finds nothing to do and says nothing.
        again = _run_tidy(tmp_path, inst, mw)
        assert "Removed " not in again

    def test_old_image_leaves_file(self, tmp_path):
        mw, _ = _mediawiki(str(tmp_path), new_image=False)
        inst = _instance(str(tmp_path), OLD_COPY)
        out = _run_tidy(tmp_path, inst, mw)
        assert _read(inst) == OLD_COPY
        assert "Removed " not in out

    def test_stopped_instance_is_skipped(self, tmp_path):
        mw, _ = _mediawiki(str(tmp_path))
        inst = _instance(str(tmp_path), OLD_COPY)
        _run_tidy(tmp_path, inst, mw, running=False)
        assert _read(inst) == OLD_COPY

    def test_unparsable_file_warns_and_is_left(self, tmp_path):
        mw, _ = _mediawiki(str(tmp_path))
        inst = _instance(str(tmp_path), "{not json")
        out = _run_tidy(tmp_path, inst, mw)
        assert "WARNING: Cannot parse" in out
        with open(os.path.join(inst, "config", "composer.local.json")) as f:
            assert f.read() == "{not json"

    def test_gitops_deletion_is_staged(self, tmp_path):
        mw, _ = _mediawiki(str(tmp_path))
        inst = _instance(str(tmp_path), {"extra": {"merge-plugin": {
            "include": [BUNDLED, BUNDLED_SKIN]}}})
        _git(inst, "init", "-q")
        _git(inst, "add", "config/composer.local.json")
        _git(inst, "commit", "-qm", "init")
        with open(os.path.join(inst, ".gitops-host"), "w") as f:
            f.write("host1\n")
        out = _run_tidy(tmp_path, inst, mw)
        assert "the file was deleted" in out
        assert "publish it with 'canasta gitops push'" in out
        assert _git(inst, "diff", "--cached", "--name-status").split() == [
            "D", "config/composer.local.json"]

    def test_gitops_edit_is_staged(self, tmp_path):
        mw, _ = _mediawiki(str(tmp_path))
        inst = _instance(str(tmp_path), OLD_COPY)
        _git(inst, "init", "-q")
        _git(inst, "add", "config/composer.local.json")
        _git(inst, "commit", "-qm", "init")
        with open(os.path.join(inst, ".gitops-host"), "w") as f:
            f.write("host1\n")
        _run_tidy(tmp_path, inst, mw)
        assert _git(inst, "diff", "--cached", "--name-status").split() == [
            "M", "config/composer.local.json"]


class TestWiring:
    def test_runs_after_the_restart(self):
        with open(MAIN) as f:
            names = [t.get("name") for t in yaml.safe_load(f)]
        assert names.index("Tidy composer.local.json") > names.index(
            "Restart containers")
        # Before the stage-only report, which counts what is staged.
        assert names.index("Tidy composer.local.json") < names.index(
            "Leave the deploy of a gitops-managed instance to Argo CD")
