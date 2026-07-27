"""`canasta gitops pull` must be able to resolve a diverged branch.

Hosts contribute small config commits to one shared branch, so divergence is
routine: another host pushes while this one has a local commit. A plain
`git pull` honors the host's ambient pull.rebase / pull.ff config and could
abort with git's own hints, leaving no canasta command that resolves the
state — `gitops push` tells the operator to run `gitops pull`, and `gitops
pull` failed on exactly that. Pull with an explicit --rebase, and never leave
the repository mid-rebase.
"""

import glob
import os

import yaml


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TASKS = os.path.join(REPO_ROOT, "roles", "gitops", "tasks")
PULLS = ("pull_compose.yml", "pull_kubernetes.yml")


def _tasks(name):
    with open(os.path.join(TASKS, name)) as fh:
        return yaml.safe_load(fh)


def _cmds(tasks):
    out = []
    for t in tasks:
        c = t.get("ansible.builtin.command") or t.get("command") or {}
        if isinstance(c, dict) and c.get("cmd"):
            out.append((t, c["cmd"]))
    return out


def _pull_task(name):
    for task, cmd in _cmds(_tasks(name)):
        if "git" in cmd and " pull" in cmd:
            return task, cmd
    return None, None


def test_pull_rebases_explicitly():
    for name in PULLS:
        task, cmd = _pull_task(name)
        assert task is not None, "%s: expected a git pull task" % name
        assert "--rebase" in cmd, (
            "%s: pull must pass --rebase explicitly so a diverged branch is "
            "resolved regardless of the host's ambient git config" % name
        )
        # Replayed commits must not depend on the user's signing setup (#668).
        assert "commit.gpgsign=false" in cmd, name


def test_a_failed_replay_is_rolled_back_and_explained():
    for name in PULLS:
        tasks = _tasks(name)
        pull_task, _ = _pull_task(name)
        # The pull must not hard-fail, or the rollback below never runs.
        assert pull_task.get("failed_when") is False, (
            "%s: the pull must be caught so a stuck rebase can be aborted" % name
        )

        abort = next(
            (t for t, c in _cmds(tasks) if "rebase --abort" in c), None
        )
        assert abort is not None, (
            "%s: a conflicting replay must be rolled back, not left mid-rebase"
            % name
        )
        assert abort.get("failed_when") is False, (
            "%s: 'no rebase in progress' must not itself fail the play" % name
        )

        fail = next(
            (t for t in tasks if "ansible.builtin.fail" in t
             and "could not be replayed" in str(t["ansible.builtin.fail"]["msg"])),
            None,
        )
        assert fail is not None, "%s: expected an explanatory failure" % name
        msg = " ".join(str(fail["ansible.builtin.fail"]["msg"]).split())
        assert "rolled back" in msg and "intact" in msg, (
            "%s: the message must say the local commits survived" % name
        )


def test_init_and_join_configure_rebase_to_match():
    # The ambient config an operator hits running raw git must agree with what
    # `canasta gitops pull` does.
    configured = []
    for path in sorted(glob.glob(os.path.join(TASKS, "*.yml"))):
        with open(path) as fh:
            for _, cmd in _cmds(yaml.safe_load(fh) or []):
                if "pull.rebase" in cmd:
                    configured.append((os.path.basename(path), cmd))
    assert configured, "expected init/join to configure a pull strategy"
    wrong = [(f, c) for f, c in configured if "pull.rebase true" not in c]
    assert not wrong, (
        "these set a pull strategy that contradicts `gitops pull --rebase`:\n"
        + "\n".join("  %s: %s" % (f, c) for f, c in wrong)
    )
