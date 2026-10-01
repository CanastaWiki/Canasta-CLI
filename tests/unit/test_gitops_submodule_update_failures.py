"""A failed submodule update stops `gitops pull` and `gitops join`.

`git submodule update --init --recursive` exits 0 in a repo with no
submodules, so a non-zero exit is always a real failure. Pull and join run
it through _update_submodules.yml, which fails with git's own error before
the caller records the pull as applied or commits and pushes the join.
"""

import os
import subprocess

import pytest
import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
GITOPS_TASKS = os.path.join(REPO_ROOT, "roles", "gitops", "tasks")
HELPER = os.path.join(GITOPS_TASKS, "_update_submodules.yml")
CALLERS = [
    "pull_compose.yml",
    "pull_kubernetes.yml",
    "join.yml",
    "join_kubernetes.yml",
]


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f) or []


def _walk(items):
    for item in items:
        if not isinstance(item, dict):
            continue
        yield item
        for key in ("block", "rescue", "always"):
            if key in item:
                yield from _walk(item[key])


def _names(tasks):
    return [t.get("name", "") for t in _walk(tasks)]


def _includes_helper(task):
    return "_update_submodules.yml" in str(
        task.get("ansible.builtin.include_tasks", ""))


def _helper_task(name):
    return next(t for t in _load(HELPER) if t.get("name") == name)


def git(cwd, *args, check=True):
    return subprocess.run(
        ["git", "-c", "protocol.file.allow=always",
         "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=cwd, check=check, capture_output=True, text=True,
    )


def _run_helper_cmd(cwd):
    cmd = _helper_task("Update submodules")["ansible.builtin.command"]["cmd"]
    return subprocess.run(
        ["bash", "-c", cmd], cwd=cwd, capture_output=True, text=True)


class TestHelper:
    def test_command_failure_is_left_to_the_named_fail_task(self):
        task = _helper_task("Update submodules")
        assert "ignore_errors" not in task
        assert task["failed_when"] is False
        assert task["register"] == "_submodule_update"

    def test_a_non_zero_exit_fails(self):
        fail = _helper_task("Fail if the submodules could not be updated")
        assert "ansible.builtin.fail" in fail
        assert fail["when"] == "_submodule_update.rc != 0"

    def test_the_failure_names_the_instance_and_git_error(self):
        fail = _helper_task("Fail if the submodules could not be updated")
        templar = Templar(loader=DataLoader(), variables={
            "instance_path": "/srv/canasta/site",
            "_submodule_update": {
                "rc": 1, "stdout": "",
                "stderr": "fatal: clone of 'x' into submodule path "
                          "'extensions/X' failed\n",
            },
        })
        msg = templar.template(
            trust_as_template(fail["ansible.builtin.fail"]["msg"]))
        assert "/srv/canasta/site" in msg
        assert "extensions/X" in msg


class TestGitBehavior:
    """The premise: no submodules exits 0, a broken one does not."""

    def test_no_submodules_exits_zero(self, tmp_path):
        git(tmp_path, "init", "-q", "-b", "main")
        (tmp_path / "f").write_text("x\n")
        git(tmp_path, "add", ".")
        git(tmp_path, "commit", "-q", "-m", "init")
        assert _run_helper_cmd(tmp_path).returncode == 0

    def test_an_unreachable_submodule_exits_non_zero(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        git(src, "init", "-q", "-b", "main")
        (src / "extension.json").write_text("{}\n")
        git(src, "add", ".")
        git(src, "commit", "-q", "-m", "ext")

        top = tmp_path / "top"
        top.mkdir()
        git(top, "init", "-q", "-b", "main")
        git(top, "submodule", "add", "-q", src.as_uri(), "extensions/X")
        git(top, "commit", "-q", "-m", "add submodule")

        clone = tmp_path / "clone"
        git(tmp_path, "clone", "-q", str(top), str(clone))
        # The submodule's source disappears before this host fetches it.
        subprocess.run(["rm", "-rf", str(src)], check=True)
        assert _run_helper_cmd(clone).returncode != 0


class TestCallers:
    def test_no_gitops_task_hides_a_submodule_update_failure(self):
        offending = []
        for filename in sorted(os.listdir(GITOPS_TASKS)):
            if not filename.endswith(".yml"):
                continue
            for task in _walk(_load(os.path.join(GITOPS_TASKS, filename))):
                cmd = str(task.get("ansible.builtin.command", ""))
                if "git submodule update" not in cmd:
                    continue
                if filename == "_update_submodules.yml":
                    continue
                if task.get("ignore_errors") or "failed_when" in task:
                    offending.append("%s: %s" % (filename, task.get("name")))
        assert not offending, offending

    @pytest.mark.parametrize("filename", CALLERS)
    def test_caller_uses_the_helper(self, filename):
        tasks = _load(os.path.join(GITOPS_TASKS, filename))
        assert any(_includes_helper(t) for t in _walk(tasks))
        assert not any(
            "git submodule update" in str(t.get("ansible.builtin.command", ""))
            for t in _walk(tasks))

    @pytest.mark.parametrize("filename", CALLERS)
    def test_helper_is_not_conditional(self, filename):
        tasks = _load(os.path.join(GITOPS_TASKS, filename))
        for t in tasks:
            if _includes_helper(t):
                assert "when" not in t

    def test_pull_updates_submodules_before_recording_the_pull(self):
        names = _names(_load(os.path.join(GITOPS_TASKS, "pull_compose.yml")))
        assert names.index("Update submodules") < names.index(
            "Write .gitops-applied")

    def test_pull_kubernetes_updates_submodules_before_rendering(self):
        names = _names(
            _load(os.path.join(GITOPS_TASKS, "pull_kubernetes.yml")))
        assert names.index("Update submodules") < names.index(
            "Render per-host values")

    @pytest.mark.parametrize("filename", ["join.yml", "join_kubernetes.yml"])
    def test_join_updates_submodules_before_pushing(self, filename):
        tasks = _load(os.path.join(GITOPS_TASKS, filename))
        names = _names(tasks)
        last_update = max(
            i for i, t in enumerate(_walk(tasks)) if _includes_helper(t))
        assert last_update < names.index("Push to remote")
