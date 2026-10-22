"""Compose gitops pull commits what its own .env sync changed.

The sync writes direct .env edits into env.template and the host vars and
stages them. Those changes must not stop the pull that made them: they are
committed before the rebase, like any local commit. A change the operator
already had to a tracked file still stops the pull, as does a sync change on
a host that cannot push.
"""

import base64
import os
import subprocess

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
PULL = os.path.join(REPO_ROOT, "roles", "gitops", "tasks", "pull_compose.yml")


def _tasks():
    with open(PULL) as f:
        return yaml.safe_load(f)


def _task(name):
    return next(t for t in _tasks() if t.get("name") == name)


def _names():
    return [t.get("name") for t in _tasks()]


def _template(expr, variables):
    return Templar(loader=DataLoader(), variables=variables).template(
        trust_as_template(expr))


def _when(name, variables):
    conds = _task(name)["when"]
    return all(_template("{{ %s }}" % c, variables) for c in conds)


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "commit.gpgsign=false", *args],
        check=True, capture_output=True, text=True).stdout


def _status(repo):
    out = _git(repo, "status", "--porcelain", "--untracked-files=no")
    return {"stdout": out.rstrip("\n"), "stdout_lines": out.splitlines()}


def _b64(text):
    return base64.b64encode(text.encode()).decode()


def _repo(tmp_path, role="both"):
    inst = tmp_path / "inst"
    (inst / "hosts" / "h1").mkdir(parents=True)
    (inst / "config").mkdir()
    _git(inst, "init", "-q")
    _git(inst, "config", "user.email", "t@example.com")
    _git(inst, "config", "user.name", "t")
    (inst / "env.template").write_text("HTTP_PORT=80\n")
    (inst / "hosts" / "h1" / "vars.yaml").write_text("a: 1\n")
    (inst / "hosts" / "hosts.yaml").write_text(
        "hosts:\n  - name: h1\n    role: %s\n" % role)
    (inst / "config" / "Local.php").write_text("<?php\n")
    _git(inst, "add", "-A")
    _git(inst, "commit", "-q", "-m", "init")
    return inst


def _sync(inst):
    (inst / "env.template").write_text("HTTP_PORT=80\nMYSQL_HOST=db\n")
    _git(inst, "add", "env.template")


def _pull_vars(inst, before):
    v = {
        "instance_path": str(inst),
        "_pull_dirty_before_sync": before,
        "_pull_uncommitted": _status(inst),
        "_sync_env_host_raw": {"content": _b64("h1\n")},
    }
    sep = _task("Separate the .env sync's changes from earlier ones")
    v["_pull_sync_paths"] = _template(
        sep["ansible.builtin.set_fact"]["_pull_sync_paths"],
        dict(v, **{k: _template(x, v) for k, x in sep["vars"].items()}))
    if v["_pull_sync_paths"]:
        v["_pull_hosts_raw"] = {
            "content": _b64((inst / "hosts" / "hosts.yaml").read_text())}
    else:
        v["_pull_hosts_raw"] = {"skipped": True}
    dec = _task("Decide whether to commit the .env sync's changes")
    v["_pull_commit_sync"] = _template(
        dec["ansible.builtin.set_fact"]["_pull_commit_sync"], v)
    return v


def _commit(v):
    argv = _template(
        _task("Commit the .env sync's changes")["ansible.builtin.command"]["argv"],
        v)
    subprocess.run(argv, cwd=v["instance_path"], check=True,
                   capture_output=True)


def test_sync_changes_are_committed_and_do_not_stop_the_pull(tmp_path):
    inst = _repo(tmp_path)
    before = _status(inst)
    _sync(inst)
    v = _pull_vars(inst, before)

    assert v["_pull_sync_paths"] == ["env.template"]
    assert v["_pull_commit_sync"] is True
    assert not _when("Fail if uncommitted changes exist", v)
    assert _when("Commit the .env sync's changes", v)

    _commit(v)
    assert _git(inst, "show", "--name-only", "--format=", "HEAD").split() == [
        "env.template"]
    assert _status(inst)["stdout"] == ""


def test_an_earlier_edit_of_a_sync_file_still_stops_the_pull(tmp_path):
    inst = _repo(tmp_path)
    (inst / "env.template").write_text("HTTP_PORT=8080\n")
    before = _status(inst)
    _sync(inst)
    v = _pull_vars(inst, before)

    assert v["_pull_sync_paths"] == []
    assert v["_pull_commit_sync"] is False
    assert _when("Fail if uncommitted changes exist", v)


def test_an_earlier_staged_edit_of_a_sync_file_still_stops_the_pull(tmp_path):
    inst = _repo(tmp_path)
    (inst / "env.template").write_text("HTTP_PORT=8080\n")
    _git(inst, "add", "env.template")
    before = _status(inst)
    _sync(inst)
    v = _pull_vars(inst, before)

    assert v["_pull_sync_paths"] == []
    assert _when("Fail if uncommitted changes exist", v)


def test_an_earlier_edit_of_another_file_still_stops_the_pull(tmp_path):
    inst = _repo(tmp_path)
    (inst / "config" / "Local.php").write_text("<?php // edited\n")
    before = _status(inst)
    _sync(inst)
    v = _pull_vars(inst, before)

    assert v["_pull_sync_paths"] == ["env.template"]
    assert _when("Fail if uncommitted changes exist", v)


def test_a_host_that_cannot_push_keeps_refusing(tmp_path):
    inst = _repo(tmp_path, role="sink")
    before = _status(inst)
    _sync(inst)
    v = _pull_vars(inst, before)

    assert v["_pull_sync_paths"] == ["env.template"]
    assert v["_pull_commit_sync"] is False
    assert _when("Fail if uncommitted changes exist", v)
    assert not _when("Commit the .env sync's changes", v)


def test_clean_tree_neither_fails_nor_commits(tmp_path):
    inst = _repo(tmp_path)
    before = _status(inst)
    v = _pull_vars(inst, before)

    assert not _when("Fail if uncommitted changes exist", v)
    assert not _when("Commit the .env sync's changes", v)


def test_order_snapshot_sync_check_commit_then_pull():
    names = _names()
    pull_i = names.index("Pull from remote")
    sync_i = next(i for i, t in enumerate(_tasks())
                  if "sync_env.yml" in str(t.get("ansible.builtin.include_tasks", "")))
    assert names.index("List tracked files changed before the .env sync") \
        < sync_i \
        < names.index("Check for uncommitted changes to tracked files") \
        < names.index("Fail if uncommitted changes exist") \
        < names.index("Guard against committing unencrypted git-crypt files") \
        < names.index("Commit the .env sync's changes") \
        < names.index("Record the .env sync's commit as applied") \
        < names.index("Get current commit hash") \
        < pull_i


def test_the_commit_is_reported_with_a_push():
    msg = " ".join(_task("Report the .env sync's commit")
                   ["ansible.builtin.debug"]["msg"].split())
    assert "Committed" in msg and "canasta gitops push" in msg


def test_refusals_discard_staged_changes_too():
    # The .env sync stages what it writes, which `git checkout -- <file>`
    # leaves in place.
    for name in ("pull_compose.yml", "_explain_pull_failure.yml"):
        with open(os.path.join(REPO_ROOT, "roles", "gitops", "tasks", name)) as f:
            text = f.read()
        assert "'git checkout HEAD -- <file>'" in text
        assert "'git checkout -- <file>'" not in text
