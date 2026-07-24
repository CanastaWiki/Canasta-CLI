"""Regression guard for #1157: `gitops init` must fail early on an
unreachable/unauthorized remote, before any local mutation.

The Step 4 `git ls-remote` must not swallow access failures — a permission /
network failure (non-zero rc) must be distinguishable from a reachable empty
remote, and a fail task keyed on that rc must stop init before it converts
submodules / inits the repo / commits (which would leave a committed .git that
forces a --reinit). Applies to both the Compose and Kubernetes init paths.
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
INIT_FILES = [
    os.path.join(REPO_ROOT, "roles", "gitops", "tasks", "init_compose.yml"),
    os.path.join(REPO_ROOT, "roles", "gitops", "tasks", "init_kubernetes.yml"),
]


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f) or []


def _walk(tasks):
    for t in tasks or []:
        if not isinstance(t, dict):
            continue
        yield t
        for nested in ("block", "rescue", "always"):
            if nested in t:
                yield from _walk(t[nested])


def _cmd_text(t):
    c = t.get("ansible.builtin.command") or t.get("command") or {}
    if not isinstance(c, dict):
        return str(c)
    return c.get("cmd", "")


def _when_text(t):
    w = t.get("when")
    if not w:
        return ""
    return " ".join(w) if isinstance(w, list) else str(w)


class TestInitFailsEarlyOnUnreachableRemote:
    def test_each_init_path_fails_on_unreachable_remote(self):
        for path in INIT_FILES:
            tasks = list(_walk(_load(path)))
            checks = [t for t in tasks if "ls-remote" in _cmd_text(t)]
            assert checks, (
                "%s must probe the remote with git ls-remote" % path)
            reg = checks[0].get("register")
            assert reg, "%s: the ls-remote check must register its result" % path

            # The rc must be inspectable (not aborted mid-play): the check uses
            # failed_when: false rather than plain success.
            assert checks[0].get("failed_when") is False, (
                "%s: the ls-remote check must use `failed_when: false` so its "
                "rc can be inspected instead of aborting (#1157)" % path)

            fails = [t for t in tasks
                     if "ansible.builtin.fail" in t or "fail" in t]
            rc_guard = [t for t in fails
                        if reg in _when_text(t) and "rc" in _when_text(t)]
            assert rc_guard, (
                "%s must fail early when the remote is unreachable — a fail "
                "task keyed on %s.rc != 0, before any local mutation (#1157)"
                % (path, reg))
