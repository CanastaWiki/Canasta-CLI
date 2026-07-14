"""Regression guard: gitops must heal dangling submodule gitlinks after a move.

A backup/restore move to a new host carries each submodule's `.git` gitlink file
(pointing at ``../../.git/modules/<path>``) but not the top-level ``.git/modules``
backing, so every subsequent git op aborts with
``fatal: not a git repository: <path>/../../.git/modules/<path>``. Both
`gitops join` (fresh move) and `gitops fix-submodules` (recovery) must drop the
stale submodule working tree and re-clone before the first git command that
resolves a gitlink. See issue #1125.
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
TASKS = os.path.join(REPO_ROOT, "roles", "gitops", "tasks")
HEAL = os.path.join(TASKS, "_heal_dangling_submodules.yml")
JOIN = os.path.join(TASKS, "join.yml")
FIX = os.path.join(TASKS, "fix_submodules.yml")


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _walk(tasks):
    for t in tasks or []:
        if not isinstance(t, dict):
            continue
        yield t
        for nested in ("block", "rescue", "always"):
            if nested in t:
                yield from _walk(t[nested])


def _include_index(tasks, tasks_from):
    """Index (in walk order) of the task that includes ``tasks_from``."""
    for i, task in enumerate(_walk(tasks)):
        inc = task.get("ansible.builtin.include_tasks") or task.get(
            "include_tasks"
        )
        if isinstance(inc, str) and tasks_from in inc:
            return i
        if isinstance(inc, dict) and tasks_from in str(inc.get("file", "")):
            return i
    return None


def _first_cmd_index(tasks, needle):
    """Index of the first task running a git command containing ``needle``."""
    for i, task in enumerate(_walk(tasks)):
        cmd = task.get("ansible.builtin.command") or task.get("command")
        shell = task.get("ansible.builtin.shell") or task.get("shell")
        text = ""
        for c in (cmd, shell):
            if isinstance(c, str):
                text += c
            elif isinstance(c, dict):
                text += str(c.get("cmd", ""))
        if needle in text:
            return i
    return None


class TestHealTaskExists:
    def test_removes_working_tree_when_backing_missing(self):
        tasks = _load(HEAL)
        # a stat of .git/modules/<path> ...
        assert any(
            "/.git/modules/" in str(
                (t.get("ansible.builtin.stat") or t.get("stat") or {}).get(
                    "path", ""
                )
            )
            for t in _walk(tasks)
        ), "heal task must stat each submodule's .git/modules backing"
        # ... gating a file: state=absent removal.
        removals = [
            t
            for t in _walk(tasks)
            if (t.get("ansible.builtin.file") or t.get("file") or {}).get(
                "state"
            )
            == "absent"
            and "when" in t
        ]
        assert removals, (
            "heal task must remove the working tree (file state=absent) "
            "conditioned on the missing backing"
        )


class TestWiredIntoRecoveryPaths:
    def test_fix_submodules_heals_before_update(self):
        tasks = _load(FIX)
        heal = _include_index(tasks, "_heal_dangling_submodules")
        update = _first_cmd_index(tasks, "submodule update --init")
        assert heal is not None, "fix_submodules.yml must include the heal task"
        assert update is not None
        assert heal < update, (
            "heal must run before `git submodule update --init` so the "
            "re-clone isn't blocked by the dangling gitlink"
        )

    def test_join_heals_before_diff(self):
        tasks = _load(JOIN)
        heal = _include_index(tasks, "_heal_dangling_submodules")
        diff = _first_cmd_index(tasks, "diff --name-only HEAD")
        assert heal is not None, "join.yml must include the heal task"
        assert diff is not None
        assert heal < diff, (
            "heal must run before `git diff --name-only HEAD`, which is the "
            "command that aborts on a dangling gitlink after a move"
        )


class TestGitmodulesParseSplitsOnNewlines:
    """`.split('\\n')` inside a YAML folded (>-) scalar does NOT split on
    newlines — Jinja receives a literal backslash-n, so the whole file parses
    as one line and _registered_paths comes back empty. That misclassifies every
    registered submodule as an orphan: harmless-looking on healthy instances
    (the orphan block re-appends a duplicate .gitmodules block) but fatal on a
    dangling one. The parse must use `.splitlines()`."""

    def test_no_split_backslash_n_in_gitmodules_parse(self):
        with open(FIX) as f:
            text = f.read()
        assert ".split('\\n')" not in text, (
            "fix_submodules.yml must not use .split('\\n') in a folded scalar; "
            "use .splitlines() so .gitmodules parses line by line"
        )

    def test_registered_paths_use_splitlines(self):
        with open(FIX) as f:
            text = f.read()
        # Both _registered_paths parses (initial + refreshed) read .gitmodules.
        assert text.count("| b64decode).splitlines()") >= 2, (
            "both .gitmodules parses must use .splitlines()"
        )
