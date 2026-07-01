"""Regression guard for 'canasta gitops rm' untrack scope and safety.

'gitops rm' is the inverse of the well-tested 'gitops add' (see
test_gitops_add_scope). It must:
  * untrack a named path with `git rm --cached --ignore-unmatch` — `--cached`
    so git never touches the working tree (the on-disk delete is a separate,
    explicit step), and `--ignore-unmatch` so removing an already-untracked
    path is idempotent rather than a hard failure;
  * gate the on-disk delete on an explicit path (never a whole-tree wipe); and
  * stage pending deletions with `git add -u` (deletions only), never a
    whole-tree `git add -A` / `git add .`.
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
RM_YML = os.path.join(REPO_ROOT, "roles", "gitops", "tasks", "rm.yml")


def _as_list(when):
    if not when:
        return []
    return when if isinstance(when, list) else [str(when)]


def _walk_tasks(tasks, inherited=()):
    """Yield (task, effective_when) with block-level `when` propagated down to
    nested tasks, matching how Ansible applies a block's condition."""
    for t in tasks or []:
        if not isinstance(t, dict):
            continue
        effective = list(inherited) + _as_list(t.get("when"))
        yield t, effective
        for nested in ("block", "rescue", "always"):
            if nested in t:
                yield from _walk_tasks(t[nested], effective)


def _load_tasks():
    with open(RM_YML) as f:
        return list(_walk_tasks(yaml.safe_load(f)))


def _cmd(task):
    c = task.get("ansible.builtin.command") or task.get("command") or {}
    return c.get("cmd", "") if isinstance(c, dict) else str(c)


class TestGitopsRmScope:
    def test_untrack_uses_cached_and_ignore_unmatch(self):
        rm_cmds = [_cmd(t) for t, _w in _load_tasks() if "git rm" in _cmd(t)]
        assert rm_cmds, "rm.yml must untrack via a `git rm` command"
        for c in rm_cmds:
            assert "--cached" in c, (
                "`git rm` must use --cached so it never deletes from the "
                "working tree (the on-disk delete is a separate step): %r" % c)
            assert "--ignore-unmatch" in c, (
                "`git rm` must use --ignore-unmatch so untracking an "
                "already-untracked path is idempotent: %r" % c)

    def test_disk_delete_is_path_scoped(self):
        """The on-disk file removal must be guarded by an explicit path so it
        can never wipe the whole instance tree."""
        deletes = [
            (t, w) for t, w in _load_tasks()
            if (t.get("ansible.builtin.file") or t.get("file") or {})
            .get("state") == "absent"
        ]
        assert deletes, "rm.yml must delete the named file from disk"
        for t, effective_when in deletes:
            cond = " ".join(effective_when)
            assert "path is defined" in cond, (
                "the on-disk delete must be gated on `path is defined` "
                "(directly or via its block): %r" % t.get("name"))

    def test_no_whole_tree_stage(self):
        offenders = [_cmd(t) for t, _w in _load_tasks()
                     if "git add -A" in _cmd(t) or "git add ." in _cmd(t)]
        assert not offenders, (
            "gitops rm must stage deletions with `git add -u`, not a "
            "whole-tree add: %r" % offenders)
