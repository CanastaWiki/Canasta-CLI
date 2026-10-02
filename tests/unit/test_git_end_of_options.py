"""Operator-supplied branches and refs never reach git as options.

`extension add --branch` and `extension set-version --ref` are passed to
`git fetch` / `git checkout`. Shell quoting stops metacharacters but not a
value that starts with '-', which git parses as an option (for example
`--upload-pack=<command>` on fetch). Each command puts --end-of-options
before the value, and a leading '-' is refused up front.
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
TASKS = os.path.join(REPO_ROOT, "roles", "extensions_skins", "tasks")


def _walk(tasks):
    for t in tasks or []:
        if not isinstance(t, dict):
            continue
        yield t
        for key in ("block", "rescue", "always"):
            yield from _walk(t.get(key))


def _commands(name):
    with open(os.path.join(TASKS, name)) as f:
        tasks = yaml.safe_load(f)
    return [t["ansible.builtin.command"]["cmd"] for t in _walk(tasks)
            if isinstance(t.get("ansible.builtin.command"), dict)]


def test_add_fetches_and_checks_out_the_branch_after_end_of_options():
    uses = [c for c in _commands("_add_one.yml")
            if "item.branch | quote" in c
            and (" fetch " in c or " checkout " in c)]
    assert len(uses) == 3
    for cmd in uses:
        assert "--end-of-options" in cmd
        assert cmd.index("--end-of-options") < cmd.index("item.branch")


def test_set_version_checks_out_the_ref_after_end_of_options():
    uses = [c for c in _commands("set_version.yml") if "ref | quote" in c]
    assert len(uses) == 1
    assert "checkout --end-of-options {{ ref | quote }}" in uses[0]


def test_set_version_refuses_a_ref_starting_with_a_dash():
    with open(os.path.join(TASKS, "set_version.yml")) as f:
        tasks = yaml.safe_load(f)
    names = [t.get("name") for t in tasks]
    check = tasks[names.index("Validate the ref")]
    assert check["when"] == "ref | trim is match('-')"
    first_git = next(i for i, t in enumerate(tasks)
                     if "git " in str(t.get("ansible.builtin.command", "")))
    assert names.index("Validate the ref") < first_git
