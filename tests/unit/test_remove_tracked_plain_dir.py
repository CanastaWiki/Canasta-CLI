"""extension/skin remove treats a tracked plain directory as a plain directory.

A copy committed to the gitops repo as plain files is not a submodule, so it
must skip submodule deinit and `git rm` and take the plain-directory path.
"""

import os
import shlex
import subprocess

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
REMOVE = os.path.join(REPO_ROOT, "roles", "extensions_skins", "tasks",
                      "remove.yml")


def _walk(tasks):
    for t in tasks or []:
        if not isinstance(t, dict):
            continue
        yield t
        for nested in ("block", "rescue", "always"):
            if nested in t:
                yield from _walk(t[nested])


def _task(name_prefix):
    with open(REMOVE) as f:
        tasks = yaml.safe_load(f)
    return next(t for t in _walk(tasks)
                if str(t.get("name", "")).startswith(name_prefix)
                and "block" not in t)


def _templar(variables):
    return Templar(loader=DataLoader(), variables=variables)


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "protocol.file.allow=always", *args],
        check=True, capture_output=True, text=True).stdout


def _instance(tmp_path):
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    _git(upstream, "init", "-q")
    _git(upstream, "-c", "user.name=t", "-c", "user.email=t@example.com",
         "commit", "-q", "--allow-empty", "-m", "init")
    inst = tmp_path / "inst"
    (inst / "extensions" / "Plain").mkdir(parents=True)
    (inst / "extensions" / "Plain" / "extension.json").write_text("{}\n")
    _git(inst, "init", "-q")
    _git(inst, "config", "user.email", "t@example.com")
    _git(inst, "config", "user.name", "t")
    _git(inst, "add", "-A")
    _git(inst, "commit", "-q", "-m", "init")
    _git(inst, "submodule", "add", "-q", str(upstream), "extensions/Sub")
    _git(inst, "commit", "-q", "-m", "submodule")
    return inst


def _check(inst, name):
    task = _task("Check whether each")
    variables = {"instance_path": str(inst), "_item_dir": "extensions",
                 "item": name}
    cmd = _templar(variables).template(
        trust_as_template(task["ansible.builtin.command"]["cmd"]))
    proc = subprocess.run(shlex.split(cmd), capture_output=True, text=True)
    return {"item": name, "rc": proc.returncode, "stdout": proc.stdout}


def _deinits(result):
    when = _task("Deinit registered submodules")["when"]
    return _templar({"item": result}).template(
        trust_as_template("{{ " + when + " }}"))


def test_tracked_plain_directory_is_not_deinited(tmp_path):
    inst = _instance(tmp_path)
    result = _check(inst, "Plain")
    assert result["rc"] == 0
    assert _deinits(result) is False


def test_registered_submodule_is_deinited(tmp_path):
    inst = _instance(tmp_path)
    assert _deinits(_check(inst, "Sub")) is True


def test_untracked_directory_is_not_deinited(tmp_path):
    inst = _instance(tmp_path)
    (inst / "extensions" / "Loose").mkdir()
    assert _deinits(_check(inst, "Loose")) is False


def test_tracked_plain_directory_removal_is_staged(tmp_path):
    inst = _instance(tmp_path)
    task = _task("Stage removed")
    variables = {"instance_path": str(inst), "_item_dir": "extensions",
                 "item": "Plain"}
    subprocess.run(["rm", "-rf", str(inst / "extensions" / "Plain")],
                   check=True)
    cmd = _templar(variables).template(
        trust_as_template(task["ansible.builtin.command"]["cmd"]))
    subprocess.run(shlex.split(cmd), check=True)
    staged = _git(inst, "diff", "--cached", "--name-status").split("\n")
    assert "D\textensions/Plain/extension.json" in staged
