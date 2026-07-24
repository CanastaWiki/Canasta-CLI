"""Regression guard: `gitops init` must fail early on an
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
            # An init file may run more than one `git ls-remote` (e.g. a
            # deploy-key authorization probe that legitimately uses
            # ignore_errors). We only require that the *empty/reachability*
            # check — the one whose registered rc a fail task keys on — inspects
            # rc via failed_when: false and hard-fails when it is non-zero.
            checks = [t for t in tasks
                      if "ls-remote" in _cmd_text(t) and t.get("register")]
            assert checks, (
                "%s must probe the remote with a registered git ls-remote"
                % path)
            fails = [t for t in tasks
                     if "ansible.builtin.fail" in t or "fail" in t]

            reachability = [
                c for c in checks
                if c.get("failed_when") is False
                and any(c["register"] in _when_text(f) and "rc" in _when_text(f)
                        for f in fails)
            ]
            assert reachability, (
                "%s must fail early when the remote is unreachable — an "
                "ls-remote check with `failed_when: false` plus a fail task "
                "keyed on its rc != 0, before any local mutation"
                % path)
