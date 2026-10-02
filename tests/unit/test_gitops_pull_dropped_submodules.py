"""`gitops pull` removes the checkouts of submodules the pull dropped.

Git takes a removed submodule's gitlink out of the index but leaves its
checkout on disk. _remove_dropped_submodules.yml deletes such checkouts when
they are clean, and keeps (and reports) any with local changes. These tests
run the task's script against a real repository.
"""

import os
import subprocess

import pytest
import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
TASKS = os.path.join(
    REPO_ROOT, "roles", "gitops", "tasks", "_remove_dropped_submodules.yml"
)
PULL = os.path.join(REPO_ROOT, "roles", "gitops", "tasks", "pull_compose.yml")


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _script():
    task = next(t for t in _load(TASKS) if "ansible.builtin.shell" in t)
    return task["ansible.builtin.shell"]["cmd"]


def git(cwd, *args):
    return subprocess.run(
        ["git", "-c", "protocol.file.allow=always",
         "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=cwd, check=True, capture_output=True, text=True,
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    """A superproject with three submodules, and a commit that drops two."""
    src = tmp_path / "src"
    src.mkdir()
    git(src, "init", "-q", "-b", "main")
    (src / "extension.json").write_text("{}\n")
    git(src, "add", ".")
    git(src, "commit", "-q", "-m", "ext")

    top = tmp_path / "top"
    top.mkdir()
    git(top, "init", "-q", "-b", "main")
    for name in ("Clean", "Dirty", "Kept"):
        git(top, "submodule", "add", "-q", src.as_uri(), f"extensions/{name}")
    git(top, "commit", "-q", "-m", "add submodules")
    prev = git(top, "rev-parse", "HEAD")

    # The state a pull that dropped Clean and Dirty leaves behind: the
    # gitlinks are gone from the index, the checkouts are still on disk.
    for name in ("Clean", "Dirty"):
        git(top, "rm", "-q", "--cached", f"extensions/{name}")
    for name in ("Clean", "Dirty"):
        git(top, "config", "-f", ".gitmodules", "--remove-section",
            f"submodule.extensions/{name}")
    git(top, "add", ".gitmodules")
    git(top, "commit", "-q", "-m", "drop Clean and Dirty")
    (top / "extensions" / "Dirty" / "extension.json").write_text("{\"x\":1}\n")
    return top, prev


def run(top, prev):
    return subprocess.run(
        ["bash", "-c", _script()], cwd=top, env={**os.environ, "PREV": prev},
        capture_output=True, text=True, check=True,
    ).stdout.split("\n")


def test_clean_dropped_checkout_is_removed(repo):
    top, prev = repo
    out = run(top, prev)
    assert "removed extensions/Clean" in out
    assert not (top / "extensions" / "Clean").exists()


def test_dropped_checkout_with_local_changes_is_kept(repo):
    top, prev = repo
    out = run(top, prev)
    assert "kept extensions/Dirty" in out
    assert (top / "extensions" / "Dirty" / "extension.json").exists()


def test_submodule_still_in_the_repo_is_untouched(repo):
    top, prev = repo
    out = run(top, prev)
    assert not any("Kept" in line for line in out)
    assert (top / "extensions" / "Kept" / "extension.json").exists()


def test_already_absent_checkout_is_skipped(repo):
    top, prev = repo
    subprocess.run(["rm", "-rf", str(top / "extensions" / "Clean")], check=True)
    out = run(top, prev)
    assert not any("Clean" in line for line in out)


def test_checkout_reached_through_a_symlinked_parent_is_kept(repo, tmp_path):
    top, prev = repo
    # A sibling directory outside the instance that holds a clean checkout
    # at the same relative path as a dropped submodule.
    outside = tmp_path / "outside"
    (outside / "Clean").mkdir(parents=True)
    git(outside / "Clean", "init", "-q", "-b", "main")
    (outside / "Clean" / "keep.txt").write_text("x\n")
    git(outside / "Clean", "add", ".")
    git(outside / "Clean", "commit", "-q", "-m", "x")
    subprocess.run(["rm", "-rf", str(top / "extensions")], check=True)
    (top / "extensions").symlink_to(outside)
    out = run(top, prev)
    assert "kept extensions/Clean" in out
    assert (outside / "Clean" / "keep.txt").exists()


def test_checkout_that_is_itself_a_symlink_is_kept(repo, tmp_path):
    top, prev = repo
    target = tmp_path / "elsewhere"
    subprocess.run(["mv", str(top / "extensions" / "Clean"), str(target)],
                   check=True)
    (top / "extensions" / "Clean").symlink_to(target)
    out = run(top, prev)
    assert "kept extensions/Clean" in out
    assert (target / "extension.json").exists()


def test_pull_runs_it_right_after_updating_submodules():
    names = [t.get("name") for t in _load(PULL)]
    step = names.index("Remove submodules the pull dropped")
    assert names[step - 1] == "Update submodules"


def _report(name, stdout_lines):
    """Render one report task's condition and message for given script output."""
    task = next(t for t in _load(TASKS) if t.get("name") == name)
    variables = {"_pull_dropped_submodules": {"stdout_lines": stdout_lines}}
    variables.update(
        {k: trust_as_template(v) for k, v in task["vars"].items()})
    templar = Templar(loader=DataLoader(), variables=variables)
    shown = templar.template(trust_as_template("{{ %s }}" % task["when"]))
    msg = templar.template(
        trust_as_template(task["ansible.builtin.debug"]["msg"]))
    return shown, msg


def test_removed_checkouts_are_reported_in_one_message():
    shown, msg = _report(
        "Report submodule checkouts removed with the pull",
        ["removed extensions/A", "kept extensions/B", "removed skins/C"],
    )
    assert shown
    assert msg.startswith("Removed extensions/A, skins/C,")


def test_kept_checkouts_are_reported_as_a_warning():
    shown, msg = _report(
        "Report dropped submodule checkouts that were kept",
        ["removed extensions/A", "kept extensions/B"],
    )
    assert shown
    assert msg.startswith("WARNING: extensions/B was removed")


def test_reports_are_not_loops():
    # The CLI's output callback shows debug messages from single tasks only.
    for task in _load(TASKS):
        if "ansible.builtin.debug" in task:
            assert "loop" not in task, task["name"]
