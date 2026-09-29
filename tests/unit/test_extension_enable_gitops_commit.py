"""On a gitops instance, extension/skin enable and disable commit settings.yaml.

`extension add` committed the submodule and composer.local.json but not the
settings.yaml enable, so other hosts pulled the code without the enable.
enable.yml and disable.yml (which add uses) now commit the settings file they
change, locally only, and limit the commit to that file.
"""

import os
import re
import subprocess

import pytest
import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
TASKS = os.path.join(REPO_ROOT, "roles", "extensions_skins", "tasks")
COMMIT = os.path.join(TASKS, "_commit_settings.yml")


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _commit_block():
    return next(t for t in _load(COMMIT) if "block" in t)


@pytest.mark.parametrize("name, state, action", [
    ("enable.yml", "enable", "Enable"),
    ("disable.yml", "disable", "Disable"),
])
def test_enable_and_disable_commit_the_change(name, state, action):
    tasks = _load(os.path.join(TASKS, name))
    names = [t.get("name") for t in tasks]
    change = next(t for t in tasks
                  if (t.get("canasta_settings_yaml") or {}).get("state") == state)
    assert change["register"] == "_settings_change"
    commit = tasks[names.index(change["name"]) + 1]
    assert commit["ansible.builtin.include_tasks"].endswith(
        "roles/extensions_skins/tasks/_commit_settings.yml")
    assert commit["vars"]["_settings_action"] == action


def test_commit_only_on_gitops_and_only_when_the_file_changed():
    assert _commit_block()["when"] == [
        "_commit_settings_git.stat.exists", "_settings_change is changed"]


@pytest.mark.parametrize("wiki, path", [
    ("", "config/settings/global/settings.yaml"),
    ("docs", "config/settings/wikis/docs/settings.yaml"),
])
def test_settings_path(wiki, path):
    expr = _commit_block()["vars"]["_commit_settings_path"]
    rendered = Templar(loader=DataLoader(), variables={"wiki": wiki}).template(
        trust_as_template(expr))
    assert rendered.strip() == path


def test_nothing_in_the_role_pushes():
    git_push = re.compile(r"\bgit\b(\s+-[Cc]\s+\S+)*\s+push\b")
    for name in os.listdir(TASKS):
        with open(os.path.join(TASKS, name)) as f:
            assert not git_push.search(f.read()), name


def _rendered_commands(instance, wiki=""):
    block = _commit_block()
    variables = {
        "instance_path": instance, "wiki": wiki, "_item_type": "extensions",
        "_names": "Foo,Bar", "_settings_action": "Enable",
        "_commit_settings_path": trust_as_template(
            block["vars"]["_commit_settings_path"])}
    templar = Templar(loader=DataLoader(), variables=variables)
    return [templar.template(trust_as_template(t["ansible.builtin.command"]["cmd"]))
            for t in block["block"]]


def test_commit_leaves_other_staged_changes_staged(tmp_path):
    repo = str(tmp_path)

    def git(*args):
        return subprocess.run(["git", "-C", repo, *args], check=True,
                              capture_output=True, text=True).stdout
    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    settings = tmp_path / "config" / "settings" / "global" / "settings.yaml"
    settings.parent.mkdir(parents=True)
    settings.write_text("extensions: []\n")
    (tmp_path / "vars.yaml").write_text("a: 1\n")
    git("add", "-A")
    git("commit", "-q", "-m", "init")
    # An unrelated change is already staged when the enable runs.
    (tmp_path / "vars.yaml").write_text("a: 2\n")
    git("add", "vars.yaml")
    settings.write_text("extensions:\n- Foo\n- Bar\n")

    for cmd in _rendered_commands(repo):
        subprocess.run(cmd, shell=True, check=True, capture_output=True)

    assert git("log", "-1", "--format=%s").strip() == "Enable extensions: Foo,Bar"
    assert git("show", "--name-only", "--format=", "HEAD").split() == [
        "config/settings/global/settings.yaml"]
    assert git("diff", "--cached", "--name-only").split() == ["vars.yaml"]
