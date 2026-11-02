"""`canasta gitops pull` carries local changes to tracked files through it.

Several canasta commands change tracked files themselves (upgrade pins the
image, the .env sync writes into env.template and the host vars, add and
remove edit the templates), so a pull that refused on local changes kept
refusing on its own side effects. The pull sets the changes aside as commits,
rebases, and puts them back as the staged and unstaged changes they were.
Only a real conflict stops it, and every way it stops restores the instance,
local changes included.

The tests below run the pull's own shell steps, read from the task files,
against real repositories: a bare remote and two clones standing in for two
hosts.
"""

import os
import shutil
import subprocess
import sys

import pytest
import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TASKS = os.path.join(REPO_ROOT, "roles", "gitops", "tasks")
VARS = os.path.join(REPO_ROOT, "roles", "gitops", "vars", "main.yml")
PULLS = ("pull_compose.yml", "pull_kubernetes.yml")


def _load(name):
    with open(os.path.join(TASKS, name)) as fh:
        return yaml.safe_load(fh) or []


def _walk(tasks):
    for t in tasks or []:
        yield t
        for key in ("block", "rescue", "always"):
            yield from _walk(t.get(key))


def _named(name, task_name):
    return next(t for t in _walk(_load(name)) if t.get("name") == task_name)


def _shell(name, task_name):
    return _named(name, task_name)["ansible.builtin.shell"]["cmd"]


def _subjects():
    with open(VARS) as fh:
        v = yaml.safe_load(fh)
    return {"STAGED": v["gitops_set_aside_staged_subject"],
            "UNSTAGED": v["gitops_set_aside_unstaged_subject"]}


SET_ASIDE = _shell("_set_aside_local_changes.yml",
                   "Commit local changes out of the way of the rebase")
PUT_BACK = _shell("_put_back_local_changes.yml",
                  "Put back the local changes set aside for the pull")
BASE = _shell("_local_changes_base.yml",
              "Find the commit under the set-aside local changes")
ABORT = _shell("_explain_pull_failure.yml", "Abort an incomplete rebase")
ROLLBACK = _shell("pull_compose.yml", "Roll back to the pre-pull commit")
PULL = _shell("pull_compose.yml", "Pull from remote")


# --- repositories -----------------------------------------------------------

def _env(a, b, c):
    """env.template text with its keys far enough apart to merge separately."""
    return "A=%s\n#\n#\nB=%s\n#\n#\nC=%s\n" % (a, b, c)


def _git(repo, *args, check=True):
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "commit.gpgsign=false", *args],
        check=check, capture_output=True, text=True)


def _sh(repo, cmd, check=True, **env):
    full = dict(os.environ, **_subjects(), **env)
    r = subprocess.run(["bash", "-c", cmd], cwd=str(repo), env=full,
                       capture_output=True, text=True)
    if check:
        assert r.returncode == 0, r.stdout + r.stderr
    return r


def _identity(repo):
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "pull.rebase", "true")


@pytest.fixture
def hosts(tmp_path):
    """A shared remote with one commit and two hosts cloned from it."""
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    seed = tmp_path / "seed"
    _git(tmp_path, "clone", "-q", str(remote), str(seed))
    _identity(seed)
    (seed / "env.template").write_text(_env(1, 2, 3))
    (seed / "config").mkdir()
    (seed / "config" / "backup-schedule.yml").write_text("schedule: daily\n")
    (seed / "config" / "Local.php").write_text("<?php\n")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-q", "-m", "init")
    _git(seed, "push", "-q", "origin", "HEAD:main")
    out = []
    for name in ("other", "this"):
        h = tmp_path / name
        _git(tmp_path, "clone", "-q", str(remote), str(h))
        _identity(h)
        out.append(h)
    return out


def _push_change(host, path, text, msg="incoming"):
    (host / path).parent.mkdir(parents=True, exist_ok=True)
    (host / path).write_text(text)
    _git(host, "add", "--", path)
    _git(host, "commit", "-q", "-m", msg)
    _git(host, "push", "-q")


def _status(repo):
    return _git(repo, "status", "--porcelain", "--untracked-files=all").stdout


def _head(repo):
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def _snapshot(repo):
    """Everything a rollback must restore: HEAD, index and working tree."""
    return (_head(repo),
            _git(repo, "diff", "--cached").stdout,
            _git(repo, "diff").stdout,
            _status(repo))


def _pull(repo, refuse_gitattributes=False):
    """The pull's git steps in the order the task files run them."""
    _sh(repo, PUT_BACK)
    rollback = _sh(repo, SET_ASIDE).stdout.strip()
    try:
        r = _sh(repo, PULL, check=False)
        if r.returncode != 0:
            conflicts = _git(repo, "diff", "--name-only",
                             "--diff-filter=U").stdout.split()
            _sh(repo, ABORT, check=False)
            return "conflict", conflicts
        base = _sh(repo, BASE, REV="HEAD").stdout.strip()
        if refuse_gitattributes:
            _sh(repo, ROLLBACK.replace("{{ _pull_rollback_commit }}", rollback))
            return "refused", base
        return "pulled", base
    finally:
        _sh(repo, PUT_BACK)


# --- behavior ---------------------------------------------------------------

def test_identical_local_and_incoming_changes_reapply_as_nothing(hosts):
    other, this = hosts
    _push_change(other, "env.template", _env(9, 2, 3))
    (this / "env.template").write_text(_env(9, 2, 3))
    _git(this, "add", "env.template")

    result, _ = _pull(this)

    assert result == "pulled"
    assert _head(this) == _head(other)
    assert _status(this) == ""


def test_identical_unstaged_change_also_reapplies_as_nothing(hosts):
    other, this = hosts
    _push_change(other, "env.template", _env(9, 2, 3))
    (this / "env.template").write_text(_env(9, 2, 3))

    assert _pull(this)[0] == "pulled"
    assert _head(this) == _head(other)
    assert _status(this) == ""


def test_non_overlapping_changes_survive_and_stay_pending(hosts):
    other, this = hosts
    _push_change(other, "env.template", _env(9, 2, 3))
    (this / "env.template").write_text(_env(1, 2, 7))
    _git(this, "add", "env.template")

    assert _pull(this)[0] == "pulled"
    assert _head(this) == _head(other)
    # Both edits are in the working tree, and this host's is still staged
    # for 'gitops push'.
    assert (this / "env.template").read_text() == _env(9, 2, 7)
    assert _status(this) == "M  env.template\n"
    assert "+C=7" in _git(this, "diff", "--cached").stdout


def test_staged_and_unstaged_changes_keep_their_state(hosts):
    other, this = hosts
    _push_change(other, "env.template", _env(9, 2, 3))
    (this / "config" / "backup-schedule.yml").write_text("schedule: hourly\n")
    _git(this, "add", "config/backup-schedule.yml")
    (this / "config" / "Local.php").write_text("<?php // edited\n")
    # Staged, then edited again: index and working tree hold different text.
    (this / "env.template").write_text(_env(1, 5, 3))
    _git(this, "add", "env.template")
    (this / "env.template").write_text(_env(1, 5, 6))

    assert _pull(this)[0] == "pulled"
    assert _head(this) == _head(other)
    assert set(_status(this).splitlines()) == {"M  config/backup-schedule.yml",
                                               " M config/Local.php",
                                               "MM env.template"}
    assert _git(this, "show", ":env.template").stdout == _env(9, 5, 3)
    assert (this / "env.template").read_text() == _env(9, 5, 6)


def test_new_and_deleted_files_are_carried_through(hosts):
    other, this = hosts
    _push_change(other, "env.template", _env(9, 2, 3))
    (this / "config" / "New.php").write_text("<?php // new\n")
    _git(this, "add", "config/New.php")
    _git(this, "rm", "-q", "config/Local.php")

    assert _pull(this)[0] == "pulled"
    assert set(_status(this).splitlines()) == {"A  config/New.php",
                                               "D  config/Local.php"}


def test_untracked_files_are_left_alone(hosts):
    other, this = hosts
    _push_change(other, "env.template", _env(9, 2, 3))
    (this / "config" / "zz_local.php").write_text("<?php\n")

    assert _pull(this)[0] == "pulled"
    assert _status(this) == "?? config/zz_local.php\n"


def test_a_conflicting_change_stops_the_pull_and_changes_nothing(hosts):
    other, this = hosts
    _push_change(other, "env.template", _env(9, 2, 3))
    (this / "env.template").write_text(_env(5, 2, 3))
    _git(this, "add", "env.template")
    (this / "config" / "Local.php").write_text("<?php // edited\n")
    before = _snapshot(this)

    result, conflicts = _pull(this)

    assert result == "conflict"
    assert conflicts == ["env.template"]
    assert _snapshot(this) == before
    assert not os.path.isdir(this / ".git" / "rebase-merge")


def test_a_conflict_keeps_local_commits_and_changes(hosts):
    other, this = hosts
    _push_change(other, "env.template", _env(9, 2, 3))
    (this / "config" / "Local.php").write_text("<?php // committed\n")
    _git(this, "commit", "-q", "-am", "local commit")
    (this / "env.template").write_text(_env(5, 2, 3))
    before = _snapshot(this)

    assert _pull(this)[0] == "conflict"
    assert _snapshot(this) == before


def test_rollback_after_a_gitattributes_refusal_keeps_local_changes(hosts):
    other, this = hosts
    _push_change(other, ".gitattributes", "*.txt text\n")
    (this / "env.template").write_text(_env(1, 2, 8))
    _git(this, "add", "env.template")
    (this / "config" / "Local.php").write_text("<?php // edited\n")
    before = _snapshot(this)

    result, base = _pull(this, refuse_gitattributes=True)

    assert result == "refused"
    # The rules were checked on the pulled commit, not on the local changes.
    assert base == _head(other)
    assert _snapshot(this) == before


def test_an_interrupted_pull_is_put_back_by_the_next_one(hosts):
    other, this = hosts
    (this / "env.template").write_text(_env(1, 2, 8))
    _git(this, "add", "env.template")
    (this / "config" / "Local.php").write_text("<?php // edited\n")
    before = _snapshot(this)
    _sh(this, SET_ASIDE)
    assert _status(this) == ""

    _sh(this, PUT_BACK)

    assert _snapshot(this) == before


def test_put_back_leaves_a_stopped_rebase_alone(hosts):
    other, this = hosts
    _push_change(other, "env.template", _env(9, 2, 3))
    (this / "env.template").write_text(_env(5, 2, 3))
    _sh(this, SET_ASIDE)
    assert _sh(this, PULL, check=False).returncode != 0
    stopped = _head(this)

    assert _sh(this, PUT_BACK).stdout == ""
    assert _head(this) == stopped
    assert os.path.isdir(this / ".git" / "rebase-merge")


def test_resolving_the_conflict_puts_the_change_back(hosts):
    # What 'gitops resolve' does: replay, keep this host's version, finish.
    other, this = hosts
    _push_change(other, "env.template", _env(9, 2, 3))
    (this / "env.template").write_text(_env(5, 2, 3))
    _git(this, "add", "env.template")
    orig = _sh(this, SET_ASIDE).stdout.strip()
    assert _sh(this, PULL, check=False).returncode != 0

    _git(this, "checkout", "--theirs", "--", "env.template")
    _git(this, "add", "--", "env.template")
    r = subprocess.run(["git", "-c", "commit.gpgsign=false", "rebase", "--continue"],
                       cwd=str(this), env=dict(os.environ, GIT_EDITOR="true"),
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    prev = _sh(this, BASE, REV=orig).stdout.strip()
    _sh(this, PUT_BACK)

    assert prev != orig and _git(this, "log", "-1", "--format=%s", prev).stdout.strip() == "init"
    assert _head(this) == _head(other)
    assert _status(this) == "M  env.template\n"
    assert (this / "env.template").read_text() == _env(5, 2, 3)


def test_aborting_a_resolution_puts_the_change_back(hosts):
    other, this = hosts
    _push_change(other, "env.template", _env(9, 2, 3))
    (this / "env.template").write_text(_env(5, 2, 3))
    before = _snapshot(this)
    _sh(this, SET_ASIDE)
    assert _sh(this, PULL, check=False).returncode != 0

    _git(this, "rebase", "--abort")
    _sh(this, PUT_BACK)

    assert _snapshot(this) == before


def test_set_aside_ignores_hooks_that_reword_commits(hosts):
    _, this = hosts
    hook = this / ".git" / "hooks" / "prepare-commit-msg"
    hook.write_text("#!/bin/sh\necho reworded > \"$1\"\n")
    hook.chmod(0o755)
    (this / "env.template").write_text(_env(1, 2, 8))
    before = _snapshot(this)

    _sh(this, SET_ASIDE)
    _sh(this, PUT_BACK)

    assert _snapshot(this) == before


@pytest.mark.skipif(shutil.which("git-crypt") is None, reason="needs git-crypt")
def test_encrypted_paths_are_carried_through(tmp_path):
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    other = tmp_path / "other"
    _git(tmp_path, "clone", "-q", str(remote), str(other))
    _identity(other)
    subprocess.run(["git-crypt", "init"], cwd=other, check=True, capture_output=True)
    key = tmp_path / "gitops.key"
    subprocess.run(["git-crypt", "export-key", str(key)], cwd=other, check=True)
    (other / ".gitattributes").write_text("hosts/** filter=git-crypt diff=git-crypt\n")
    (other / "hosts" / "h1").mkdir(parents=True)
    (other / "hosts" / "h1" / "vars.yaml").write_text("a: 1\nb: 2\n")
    (other / "hosts" / "h2").mkdir(parents=True)
    (other / "hosts" / "h2" / "vars.yaml").write_text("x: 1\n")
    _git(other, "add", "-A")
    _git(other, "commit", "-q", "-m", "init")
    _git(other, "push", "-q", "origin", "HEAD:main")
    this = tmp_path / "this"
    _git(tmp_path, "clone", "-q", str(remote), str(this))
    _identity(this)
    subprocess.run(["git-crypt", "unlock", str(key)], cwd=this, check=True)

    # The same staged edit on both hosts, plus an edit of another encrypted
    # file that only this host made.
    _push_change(other, "hosts/h1/vars.yaml", "a: 9\nb: 2\n")
    (this / "hosts" / "h1" / "vars.yaml").write_text("a: 9\nb: 2\n")
    _git(this, "add", "hosts/h1/vars.yaml")
    (this / "hosts" / "h2" / "vars.yaml").write_text("x: 2\n")

    assert _pull(this)[0] == "pulled"
    assert _head(this) == _head(other)
    assert _status(this) == " M hosts/h2/vars.yaml\n"
    assert (this / "hosts" / "h1" / "vars.yaml").read_text() == "a: 9\nb: 2\n"
    assert (this / "hosts" / "h2" / "vars.yaml").read_text() == "x: 2\n"
    # Stored encrypted, as git-crypt's clean filter leaves it.
    blob = subprocess.run(["git", "cat-file", "blob", "HEAD:hosts/h1/vars.yaml"],
                          cwd=this, check=True, capture_output=True).stdout
    assert blob.startswith(b"\x00GITCRYPT\x00")


def test_the_task_files_run_under_ansible(hosts, tmp_path):
    other, this = hosts
    _push_change(other, "env.template", _env(9, 2, 3))
    (this / "env.template").write_text(_env(1, 2, 7))
    _git(this, "add", "env.template")
    (this / "config" / "Local.php").write_text("<?php // edited\n")
    with open(VARS) as fh:
        role_vars = yaml.safe_load(fh)
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost", "connection": "local", "gather_facts": False,
        "vars": {"instance_path": str(this), "canasta_root": REPO_ROOT,
                 "ansible_python_interpreter": sys.executable,
                 **{k: v for k, v in role_vars.items()
                    if k.startswith("gitops_set_aside_")}},
        "tasks": [
            {"ansible.builtin.include_tasks": os.path.join(TASKS, "_set_aside_local_changes.yml")},
            {"ansible.builtin.command": {"cmd": "git pull --rebase", "chdir": str(this)}},
            {"ansible.builtin.include_tasks": os.path.join(TASKS, "_local_changes_base.yml"),
             "vars": {"local_changes_rev": "{{ _pull_rollback_commit }}"}},
            {"ansible.builtin.include_tasks": os.path.join(TASKS, "_put_back_local_changes.yml")},
            {"ansible.builtin.debug": {"msg": "BASE={{ _local_changes_base }}"}},
        ],
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_HOME=str(tmp_path / "ansible-home"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env, cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr

    assert _head(this) == _head(other)
    assert set(_status(this).splitlines()) == {"M  env.template", " M config/Local.php"}
    assert (this / "env.template").read_text() == _env(9, 2, 7)
    init = _git(this, "rev-parse", "HEAD~1").stdout.strip()
    assert "BASE=%s" % init in proc.stdout


# --- structure --------------------------------------------------------------

def _block(name):
    return _named(name, "Pull with local changes carried through")


@pytest.mark.parametrize("name", PULLS)
def test_the_pull_runs_inside_the_set_aside_block(name):
    block = _block(name)
    inner = [t.get("name") for t in block["block"]]
    assert inner[0] == "Set local changes aside for the rebase"
    assert block["block"][0]["ansible.builtin.include_tasks"].endswith(
        "/_set_aside_local_changes.yml")
    assert any("pull --rebase" in str(t.get("ansible.builtin.shell", {}))
               for t in block["block"])
    assert "when" not in block, "every path out of the pull must put the changes back"
    always = block["always"]
    assert [os.path.basename(t["ansible.builtin.include_tasks"]) for t in always] == [
        "_put_back_local_changes.yml"]


@pytest.mark.parametrize("name", PULLS)
def test_submodules_are_updated_before_the_changes_are_put_back(name):
    inner = [t.get("name") for t in _block(name)["block"]]
    assert "Update submodules" in inner


@pytest.mark.parametrize("name", PULLS)
def test_an_interrupted_pull_is_put_back_first(name):
    tasks = _load(name)
    names = [t.get("name") for t in tasks]
    heal = names.index("Put back local changes an interrupted pull set aside")
    assert heal < names.index("Pull with local changes carried through")
    assert tasks[heal]["ansible.builtin.include_tasks"].endswith(
        "/_put_back_local_changes.yml")


@pytest.mark.parametrize("name", ("push_compose.yml", "push_kubernetes.yml"))
def test_push_never_pushes_set_aside_changes(name):
    tasks = _load(name)
    names = [t.get("name") for t in tasks]
    heal = names.index("Put back local changes an interrupted pull set aside")
    assert heal < names.index("Check for staged changes")


def test_renders_follow_the_put_back():
    tasks = _load("pull_compose.yml")
    names = [t.get("name") for t in tasks]
    block_i = names.index("Pull with local changes carried through")
    syncs = [i for i, t in enumerate(tasks)
             if str(t.get("ansible.builtin.include_tasks", "")).startswith("sync_")]
    assert syncs and max(syncs) < block_i
    assert names.index("Render .env from template") > block_i
    assert names.index("Render wikis.yaml from template") > block_i


def test_the_dirty_tree_refusal_and_sync_commit_are_gone():
    text = open(os.path.join(TASKS, "pull_compose.yml")).read()
    for gone in ("Fail if uncommitted changes exist",
                 "Commit the .env sync's changes",
                 "_pull_commit_sync"):
        assert gone not in text, gone


def test_resolve_hands_the_pull_both_commits():
    tasks = list(_walk(_load("resolve.yml")))
    hand = next(t for t in tasks
                if t.get("name") == "Hand the finished rebase to the pull's apply steps")
    facts = hand["ansible.builtin.set_fact"]
    assert facts["_pull_prev_commit"]["stdout"] == "{{ _local_changes_base }}"
    assert "orig_head" in facts["_pull_rollback_commit"]
    abort_put_back = next(t for t in tasks
                          if t.get("name") == "Put back the local changes the pull set aside")
    assert abort_put_back["when"] == "_resolve_action == 'abort'"
