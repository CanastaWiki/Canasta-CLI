"""Regression guard for #1166: `gitops push` must translate git's raw
non-fast-forward rejection into canasta-native guidance, not leak the raw
`hint: use 'git pull'`.

Each push path must: register the push result and not abort on it
(failed_when: false), then, on a 'non-fast-forward'/'rejected' stderr, fail
with guidance that points at `canasta gitops pull` (never raw git). Applies to
both the Compose and Kubernetes push paths.
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
PUSH_FILES = [
    os.path.join(REPO_ROOT, "roles", "gitops", "tasks", "push_compose.yml"),
    os.path.join(REPO_ROOT, "roles", "gitops", "tasks", "push_kubernetes.yml"),
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


def _fail_msg(t):
    f = t.get("ansible.builtin.fail") or t.get("fail") or {}
    return f.get("msg", "") if isinstance(f, dict) else ""


class TestPushNonFastForwardGuidance:
    def test_each_push_path_gives_canasta_guidance(self):
        for path in PUSH_FILES:
            tasks = list(_walk(_load(path)))
            pushes = [t for t in tasks
                      if _cmd_text(t).strip().startswith("git push")]
            assert pushes, "%s must run git push" % path
            # The push must be catchable (not abort the play on rejection).
            assert any(p.get("failed_when") is False and p.get("register")
                       for p in pushes), (
                "%s: the push must register its result and use "
                "`failed_when: false` so a rejection can be translated (#1166)"
                % path)

            guidance = [
                t for t in tasks
                if ("ansible.builtin.fail" in t or "fail" in t)
                and "non-fast-forward" in _when_text(t)
                and "canasta gitops pull" in _fail_msg(t)
            ]
            assert guidance, (
                "%s must, on a non-fast-forward rejection, fail with "
                "canasta-native guidance pointing at `canasta gitops pull` "
                "(#1166)" % path)


def _when_text(t):
    w = t.get("when")
    if not w:
        return ""
    return " ".join(w) if isinstance(w, list) else str(w)
