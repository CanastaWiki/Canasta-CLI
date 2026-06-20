"""Structure guards for non-destructive gitops --reinit (#663).

--reinit exists to recover an instance after a previous init/join died
mid-run, but the reinit cleanup deletes .git before the operation is
known to succeed. These tests pin the two fixes:

- join --reinit must run _reinit_cleanup *after* the clone, and must not
  trip the ".git already exists" guard, so a failed clone leaves the
  instance's existing .git intact.
- init --reinit must move .git aside before re-init and restore it in a
  rescue if the re-init fails, so unpushed local commits aren't lost.
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
TASKS = os.path.join(REPO_ROOT, "roles", "gitops", "tasks")


def _load(name):
    with open(os.path.join(TASKS, name)) as f:
        return yaml.safe_load(f)


def _cmd(task):
    """The command string of a command/shell task, else ''."""
    for key in ("ansible.builtin.command", "command",
                "ansible.builtin.shell", "shell"):
        v = task.get(key)
        if isinstance(v, dict):
            return v.get("cmd", "") or " ".join(v.get("argv", []) or [])
        if isinstance(v, str):
            return v
    return ""


def _include(task):
    for key in ("ansible.builtin.include_tasks", "include_tasks"):
        if key in task:
            v = task[key]
            return v if isinstance(v, str) else v.get("file", "")
    return ""


def _when(task):
    w = task.get("when")
    if isinstance(w, list):
        return " and ".join(str(x) for x in w)
    return str(w) if w is not None else ""


class TestJoinClonesBeforeCleanup:
    """A failed clone must not have already deleted the old .git."""

    def _assert(self, fname):
        tasks = _load(fname)
        clone_idx = next(i for i, t in enumerate(tasks)
                         if "git clone" in _cmd(t))
        cleanup_idx = next(i for i, t in enumerate(tasks)
                           if _include(t) == "_reinit_cleanup.yml")
        assert cleanup_idx > clone_idx, (
            "%s: _reinit_cleanup must run AFTER the clone so a failed clone "
            "can't strand the instance with no .git (#663)" % fname
        )

    def test_compose_join(self):
        self._assert("join.yml")

    def test_k8s_join(self):
        self._assert("join_kubernetes.yml")


class TestJoinGuardSkippedUnderReinit:
    """The '.git already exists' guard must not fire under --reinit, since
    we now keep that .git in place until the clone succeeds."""

    def _guard_when(self, fname):
        for t in _load(fname):
            fail = t.get("ansible.builtin.fail") or t.get("fail") or {}
            if "GitOps already initialized" in (fail.get("msg") or ""):
                return _when(t)
        raise AssertionError("%s: no '.git already exists' guard found" % fname)

    def test_compose_join(self):
        w = self._guard_when("join.yml")
        assert "reinit" in w and "not" in w, (
            "join guard must be gated on `not reinit`: %r" % w
        )

    def test_k8s_join(self):
        w = self._guard_when("join_kubernetes.yml")
        assert "reinit" in w and "not" in w, (
            "k8s join guard must be gated on `not reinit`: %r" % w
        )


class TestInitBacksUpAndRestoresGit:
    """init --reinit must preserve .git across a failed re-init."""

    def setup_method(self):
        self.tasks = _load("init.yml")
        self.block_idx = next(
            (i for i, t in enumerate(self.tasks) if "block" in t), None
        )
        assert self.block_idx is not None, "init.yml has no block/rescue wrapper"
        self.block = self.tasks[self.block_idx]

    @staticmethod
    def _moves_git_to_backup(cmd):
        # mv <instance>/.git <backup>
        return ("/.git" in cmd and "_init_git_backup_dir" in cmd
                and cmd.index("/.git") < cmd.index("_init_git_backup_dir"))

    @staticmethod
    def _moves_backup_to_git(cmd):
        # mv <backup> <instance>/.git
        return ("/.git" in cmd and "_init_git_backup_dir" in cmd
                and cmd.index("_init_git_backup_dir") < cmd.rindex("/.git"))

    def test_backup_runs_before_dispatch(self):
        backup_idx = next(
            i for i, t in enumerate(self.tasks)
            if self._moves_git_to_backup(_cmd(t))
        )
        assert backup_idx < self.block_idx, (
            "init must move .git aside BEFORE dispatching the re-init (#663)"
        )

    def test_dispatch_runs_inside_block(self):
        assert any(_include(t).startswith("init_") for t in self.block["block"]), (
            "the init dispatch must run inside the block so a failure triggers "
            "the rescue/restore"
        )

    def test_rescue_restores_git_and_reraises(self):
        rescue = self.block.get("rescue") or []
        assert any(
            self._moves_backup_to_git(_cmd(t)) for t in rescue
        ), "rescue must restore the backed-up .git (#663)"
        assert any(
            (t.get("ansible.builtin.fail") or t.get("fail")) for t in rescue
        ), "rescue must re-raise the init failure rather than swallow it"
