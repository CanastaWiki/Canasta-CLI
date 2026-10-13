"""extension/skin add and remove commit only the paths they changed.

On a gitops instance the commit must leave anything else that was already
staged staged and uncommitted, for the user's own commit or `gitops push`.
"""

import os
import shlex
import subprocess

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
TASKS = os.path.join(REPO_ROOT, "roles", "extensions_skins", "tasks")


def _walk(tasks):
    for t in tasks or []:
        if not isinstance(t, dict):
            continue
        yield t
        for nested in ("block", "rescue", "always"):
            if nested in t:
                yield from _walk(t[nested])


def _task(filename, name_prefix):
    with open(os.path.join(TASKS, filename)) as f:
        tasks = yaml.safe_load(f)
    return next(t for t in _walk(tasks)
                if str(t.get("name", "")).startswith(name_prefix)
                and "block" not in t)


def _run(filename, name_prefix, variables):
    cmd = _task(filename, name_prefix)["ansible.builtin.command"]["cmd"]
    rendered = Templar(loader=DataLoader(), variables=variables).template(
        trust_as_template(cmd))
    return subprocess.run(shlex.split(rendered), capture_output=True,
                          text=True)


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "protocol.file.allow=always", *args],
        check=True, capture_output=True, text=True).stdout


def _repo(tmp_path):
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    _git(upstream, "init", "-q")
    _git(upstream, "-c", "user.name=t", "-c", "user.email=t@example.com",
         "commit", "-q", "--allow-empty", "-m", "init")
    inst = tmp_path / "inst"
    (inst / "config").mkdir(parents=True)
    (inst / "extensions").mkdir()
    _git(inst, "init", "-q")
    _git(inst, "config", "user.email", "t@example.com")
    _git(inst, "config", "user.name", "t")
    (inst / "config" / "composer.local.json").write_text("{}\n")
    _git(inst, "add", "-A")
    _git(inst, "commit", "-q", "-m", "init")
    return upstream, inst


def _stage_unrelated(inst):
    (inst / "config" / "E2E.php").write_text("<?php\n")
    _git(inst, "add", "config/E2E.php")


def _committed(inst):
    return sorted(_git(inst, "show", "--name-only", "--format=", "HEAD").split())


def _staged(inst):
    return _git(inst, "diff", "--cached", "--name-only").split()


def _add_vars(inst, name):
    return {"instance_path": str(inst), "_item_type": "extensions",
            "_item_dir": "extensions", "item": {"name": name}}


def _add_submodule(upstream, inst, name):
    _git(inst, "submodule", "add", "-q", "--", str(upstream),
         f"extensions/{name}")


def test_add_commits_only_the_added_item(tmp_path):
    upstream, inst = _repo(tmp_path)
    _stage_unrelated(inst)
    _add_submodule(upstream, inst, "NumberFormat")
    variables = _add_vars(inst, "NumberFormat")

    assert _run("_add_one.yml", "Stage added", variables).returncode == 0
    assert _run("_add_one.yml", "Commit added", variables).returncode == 0

    assert _git(inst, "log", "-1", "--format=%s").strip() == (
        "Add extensions NumberFormat")
    assert _committed(inst) == [".gitmodules", "extensions/NumberFormat"]
    assert _staged(inst) == ["config/E2E.php"]


def test_realign_commits_only_the_item(tmp_path):
    upstream, inst = _repo(tmp_path)
    _add_submodule(upstream, inst, "Foo")
    _git(inst, "commit", "-q", "-m", "add Foo")
    _git(upstream, "-c", "user.name=t", "-c", "user.email=t@example.com",
         "commit", "-q", "--allow-empty", "-m", "two")
    _git(inst / "extensions" / "Foo", "pull", "-q")
    _stage_unrelated(inst)
    variables = dict(_add_vars(inst, "Foo"), **{
        "item": {"name": "Foo", "branch": "REL1_43"}})

    assert _run("_add_one.yml", "Record re-aligned", variables).returncode == 0
    assert _run("_add_one.yml", "Commit re-aligned", variables).returncode == 0

    assert _committed(inst) == ["extensions/Foo"]
    assert _staged(inst) == ["config/E2E.php"]


def test_composer_commit_is_limited_to_composer_local_json(tmp_path):
    _, inst = _repo(tmp_path)
    _stage_unrelated(inst)
    (inst / "config" / "composer.local.json").write_text('{"a": 1}\n')
    variables = {"instance_path": str(inst), "_to_add": [{"name": "Foo"}]}

    assert _run("add.yml", "Stage composer.local.json",
                variables).returncode == 0
    assert _run("add.yml", "Commit composer.local.json (gitops)",
                variables).returncode == 0

    assert _committed(inst) == ["config/composer.local.json"]
    assert _staged(inst) == ["config/E2E.php"]


def _remove(inst, present, settings_files=()):
    variables = {
        "instance_path": str(inst), "_item_type": "extensions",
        "_item_dir": "extensions", "_remove_present": list(present),
        "_remove_settings_files": list(settings_files),
        "_composer_local": {"changed": False}}
    listed = _run("remove.yml", "List the staged removal paths", variables)
    assert listed.returncode == 0, listed.stderr
    lines = listed.stdout.splitlines()
    if not lines:
        # The commit task's `when` skips it.
        return lines, None
    variables["_remove_staged_paths"] = {"stdout_lines": lines}
    return lines, _run("remove.yml", "Commit removals", variables)


def test_remove_commits_only_the_removal(tmp_path):
    upstream, inst = _repo(tmp_path)
    _add_submodule(upstream, inst, "Foo")
    settings = inst / "config" / "settings" / "global" / "settings.yaml"
    settings.parent.mkdir(parents=True)
    settings.write_text("extensions:\n- Foo\n")
    _git(inst, "add", "-A")
    _git(inst, "commit", "-q", "-m", "add Foo")
    _stage_unrelated(inst)
    _git(inst, "submodule", "deinit", "-q", "--force", "extensions/Foo")
    _git(inst, "rm", "-q", "--force", "extensions/Foo")
    settings.write_text("extensions: []\n")
    _git(inst, "add", "-A", "--", "config/settings/global/settings.yaml")

    _, result = _remove(inst, ["Foo"],
                        ["config/settings/global/settings.yaml"])

    assert result.returncode == 0, result.stderr
    assert _committed(inst) == [
        ".gitmodules", "config/settings/global/settings.yaml",
        "extensions/Foo"]
    assert _staged(inst) == ["config/E2E.php"]


def test_remove_of_an_untracked_orphan_commits_nothing(tmp_path):
    # No .gitmodules and nothing tracked under the orphan: there is nothing
    # of the removal to commit, and the unrelated staged file must not be.
    _, inst = _repo(tmp_path)
    head = _git(inst, "rev-parse", "HEAD")
    _stage_unrelated(inst)

    lines, _ = _remove(inst, ["Orphan"])

    assert lines == []
    when = _task("remove.yml", "Commit removals")["when"]
    assert "_remove_staged_paths.stdout_lines | default([]) | length > 0" in when
    assert _git(inst, "rev-parse", "HEAD") == head
    assert _staged(inst) == ["config/E2E.php"]
