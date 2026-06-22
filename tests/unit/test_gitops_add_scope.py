"""Regression guard for 'canasta gitops add' (no path).

The no-path form is meant to stage only changes under config/. It used
to run `git add -A` after chdir-ing into config/, but since Git 2.0
`git add -A` with no pathspec stages the WHOLE working tree regardless
of the current directory — so it also swept in extensions/, untracked
host files (hosts/hosts.yaml), public_assets/, etc.

The fix scopes the add with an explicit `config` pathspec. This test
asserts the no-path staging command carries that pathspec, so a revert
to the bare whole-tree form is caught.
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
ADD_YML = os.path.join(REPO_ROOT, "roles", "gitops", "tasks", "add.yml")


def _walk_tasks(tasks):
    for t in tasks or []:
        if not isinstance(t, dict):
            continue
        yield t
        for nested in ("block", "rescue", "always"):
            if nested in t:
                yield from _walk_tasks(t[nested])


def _load_tasks():
    with open(ADD_YML) as f:
        return list(_walk_tasks(yaml.safe_load(f)))


def _cmd(task):
    c = task.get("ansible.builtin.command") or task.get("command") or {}
    return c.get("cmd", "") if isinstance(c, dict) else str(c)


class TestGitopsAddScope:
    def test_no_path_add_is_scoped_to_config(self):
        """The no-path staging command must name `config` as a pathspec,
        not rely on chdir + bare `git add -A` (which is whole-tree)."""
        tasks = _load_tasks()
        # Only the whole-tree-capable `git add -A` form is at risk of the
        # chdir-ignored bug. Explicit single-file stages (e.g.
        # `git add -- wikis.yaml.template`) are intentionally scoped.
        no_path = [
            t for t in tasks
            if "path is not defined" in str(t.get("when", ""))
            and ("git add -A" in _cmd(t))
        ]
        assert no_path, "add.yml lost its no-path staging task"
        for t in no_path:
            cmd = _cmd(t)
            # A bare `git add -A` (the buggy form) has no pathspec, so
            # `config` would only appear in chdir — assert it's in the cmd.
            tokens = cmd.split()
            assert "config" in tokens, (
                "no-path 'gitops add' must stage with a `config` pathspec "
                "(e.g. `git add -A -- config`), not bare `git add -A`; got: %r"
                % cmd
            )
