"""Compose gitops enforces its encryption rules instead of trusting the repo.

What git-crypt encrypts is decided by the tracked .gitattributes, which any
commit can change. Push refuses an index whose .gitattributes lacks the
git-crypt rules init writes, pull refuses (and rolls back) a pull that would
apply such a file, and the pre-commit guard requires the git-crypt header on
every staged file under hosts/ whatever .gitattributes says.
"""

import os
import shutil
import subprocess
import sys

import pytest
import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
TASKS = os.path.join(REPO_ROOT, "roles", "gitops", "tasks")
DEFAULT = os.path.join(REPO_ROOT, "roles", "gitops", "files",
                       "gitattributes.default")

sys.path.insert(0, os.path.join(REPO_ROOT, "filter_plugins"))
from canasta_gitops import (  # noqa: E402
    FilterModule,
    canasta_gitattributes_missing_rules as missing,
)

with open(DEFAULT) as _f:
    DEFAULT_TEXT = _f.read()
HOSTS_RULE = DEFAULT_TEXT.strip()


def _load(name):
    with open(os.path.join(TASKS, name)) as f:
        return yaml.safe_load(f) or []


def _walk(tasks):
    for t in tasks or []:
        if not isinstance(t, dict):
            continue
        yield t
        for nested in ("block", "rescue", "always"):
            if nested in t:
                yield from _walk(t[nested])


def _named(tasks, name):
    return next(t for t in _walk(tasks) if t.get("name") == name)


def _index(tasks, pred):
    return next(i for i, t in enumerate(_walk(tasks)) if pred(t))


def _includes(t, name):
    return name in str(t.get("ansible.builtin.include_tasks", ""))


class TestRequiredRules:
    def test_default_encrypts_hosts(self):
        assert missing("", DEFAULT_TEXT) == [HOSTS_RULE]
        assert HOSTS_RULE.split()[0] == "hosts/**"
        assert "filter=git-crypt" in HOSTS_RULE.split()

    def test_default_satisfies_itself(self):
        assert missing(DEFAULT_TEXT, DEFAULT_TEXT) == []

    def test_missing_file_lacks_every_rule(self):
        assert missing(None, DEFAULT_TEXT) == [HOSTS_RULE]

    def test_extra_lines_are_fine(self):
        content = (
            "# assets\n"
            "*.png filter=lfs diff=lfs merge=lfs -text\n"
            + DEFAULT_TEXT
            + "*.psd filter=lfs diff=lfs merge=lfs -text\n"
        )
        assert missing(content, DEFAULT_TEXT) == []

    def test_whitespace_order_and_other_attributes_do_not_matter(self):
        assert missing("  hosts/**\tdiff=git-crypt   filter=git-crypt  \n",
                       DEFAULT_TEXT) == []

    def test_leading_slash_is_the_same_pattern(self):
        assert missing("/hosts/** filter=git-crypt\n", DEFAULT_TEXT) == []

    @pytest.mark.parametrize("content", [
        "*.png filter=lfs\n",
        "hosts/* filter=git-crypt\n",
        "hosts/** diff=git-crypt\n",
        "hosts/** filter=git-crypy\n",
        "# hosts/** filter=git-crypt\n",
    ])
    def test_absent_or_mistyped_rule_is_missing(self, content):
        assert missing(content, DEFAULT_TEXT) == [HOSTS_RULE]

    @pytest.mark.parametrize("override", [
        "hosts/** -filter",
        "hosts/** !filter",
        "hosts/** filter=lfs",
    ])
    def test_later_line_undoing_the_rule_is_missing(self, override):
        content = DEFAULT_TEXT + override + "\n"
        assert missing(content, DEFAULT_TEXT) == [HOSTS_RULE]

    def test_later_line_restoring_the_rule_counts(self):
        content = "hosts/** -filter\n" + DEFAULT_TEXT
        assert missing(content, DEFAULT_TEXT) == []

    def test_registered(self):
        assert FilterModule().filters()[
            "canasta_gitattributes_missing_rules"] is missing


class TestCheckHelper:
    def test_reads_the_given_revision(self):
        task = _named(_load("_check_gitattributes_rules.yml"),
                      "Read the tracked .gitattributes")
        assert task["ansible.builtin.command"]["cmd"] == (
            "git show {{ gitattributes_rev }}:.gitattributes")
        assert task["failed_when"] is False

    def test_required_rules_come_from_the_shipped_default(self):
        task = _named(_load("_check_gitattributes_rules.yml"),
                      "List the required encryption rules it lacks")
        expr = task["ansible.builtin.set_fact"]["_gitattributes_missing"]
        assert "canasta_gitattributes_missing_rules" in expr
        assert "/files/gitattributes.default" in expr

    def test_init_writes_the_same_default(self):
        task = _named(_load("init_compose.yml"), "Write .gitattributes")
        assert task["ansible.builtin.copy"]["src"].endswith(
            "/files/gitattributes.default")


class TestPush:
    def test_checks_the_index_before_committing(self):
        tasks = _load("push_compose.yml")
        check = _index(tasks, lambda t: _includes(
            t, "_check_gitattributes_rules.yml"))
        refuse = _index(tasks, lambda t: t.get("name")
                        == "Refuse to push without the encryption rules")
        commit = _index(tasks, lambda t: "commit -m" in str(
            t.get("ansible.builtin.command", "")))
        assert check < refuse < commit
        inc = list(_walk(tasks))[check]
        assert inc["vars"]["gitattributes_rev"] == ""

    def test_refusal_names_the_rules(self):
        task = _named(_load("push_compose.yml"),
                      "Refuse to push without the encryption rules")
        assert task["when"] == "_gitattributes_missing | length > 0"
        msg = task["ansible.builtin.fail"]["msg"]
        assert "_gitattributes_missing" in msg
        assert "canasta gitops add .gitattributes" in msg


class TestPull:
    def test_checks_after_the_pull_and_before_applying(self):
        tasks = _load("pull_compose.yml")
        pulled = _index(tasks, lambda t: t.get("name") == "Pull from remote")
        check = _index(tasks, lambda t: _includes(
            t, "_check_gitattributes_rules.yml"))
        refuse = _index(tasks, lambda t: t.get("name")
                        == "Refuse a pull that weakens encryption")
        submodules = _index(tasks, lambda t: _includes(
            t, "_update_submodules.yml"))
        render = _index(tasks, lambda t: _includes(t, "_render_env.yml"))
        applied = _index(tasks, lambda t: t.get("name")
                         == "Write .gitops-applied")
        assert pulled < check < refuse < submodules < render < applied
        inc = list(_walk(tasks))[check]
        assert inc["vars"]["gitattributes_rev"] == "HEAD"

    def test_rolls_back_to_the_pre_pull_commit_then_fails(self):
        block = _named(_load("pull_compose.yml"),
                       "Refuse a pull that weakens encryption")
        assert block["when"] == "_gitattributes_missing | length > 0"
        reset, fail = block["block"]
        assert reset["ansible.builtin.command"]["cmd"] == (
            "git reset --hard --quiet {{ _pull_prev_commit.stdout }}")
        assert "_gitattributes_missing" in fail["ansible.builtin.fail"]["msg"]


def _guard_cmd():
    task = _named(_load("_check_gitcrypt_staged.yml"),
                  "Detect staged git-crypt files stored as plaintext")
    return task["ansible.builtin.shell"]["cmd"]


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
class TestGuardBehavior:
    def _git(self, repo, *args):
        return subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
             *args],
            cwd=repo, check=True, capture_output=True, text=True)

    def _stage(self, repo, path, data):
        full = os.path.join(repo, path)
        os.makedirs(os.path.dirname(full) or repo, exist_ok=True)
        with open(full, "wb") as f:
            f.write(data)
        self._git(repo, "add", "--", path)

    def _run(self, repo):
        out = subprocess.run(["bash", "-c", _guard_cmd()], cwd=repo,
                             check=True, capture_output=True, text=True)
        return sorted(out.stdout.splitlines())

    @pytest.fixture
    def repo(self, tmp_path):
        self._git(str(tmp_path), "init", "-q")
        return str(tmp_path)

    def test_plaintext_under_hosts_is_caught_without_any_rule(self, repo):
        self._stage(repo, "hosts/h1/vars.yaml", b"secret: x\n")
        self._stage(repo, "hosts/hosts.yaml", b"hosts: []\n")
        assert self._run(repo) == ["unmarked\thosts/h1/vars.yaml",
                                   "unmarked\thosts/hosts.yaml"]

    def test_plaintext_under_hosts_is_caught_with_the_rule_overridden(
            self, repo):
        self._stage(repo, ".gitattributes",
                    (DEFAULT_TEXT + "hosts/h1/vars.yaml -filter\n").encode())
        self._stage(repo, "hosts/h1/vars.yaml", b"secret: x\n")
        assert self._run(repo) == ["unmarked\thosts/h1/vars.yaml"]

    def test_marked_path_without_an_active_filter_is_caught(self, repo):
        # No git-crypt filter is configured, so the staged blob is plaintext.
        self._stage(repo, ".gitattributes",
                    b"secrets.yaml filter=git-crypt\n" + DEFAULT_TEXT.encode())
        self._stage(repo, "secrets.yaml", b"k: v\n")
        self._stage(repo, "hosts/hosts.yaml", b"hosts: []\n")
        assert self._run(repo) == ["inactive\thosts/hosts.yaml",
                                   "inactive\tsecrets.yaml"]

    def test_encrypted_blobs_and_other_paths_pass(self, repo):
        self._stage(repo, "hosts/h1/vars.yaml",
                    b"\x00GITCRYPT\x00" + b"\x01" * 16)
        self._stage(repo, "config/LocalSettings.php", b"<?php\n")
        assert self._run(repo) == []

    def test_deleted_hosts_file_is_not_inspected(self, repo):
        self._stage(repo, "hosts/old.yaml", b"\x00GITCRYPT\x00rest")
        self._git(repo, "commit", "-qm", "seed")
        self._git(repo, "rm", "-q", "--", "hosts/old.yaml")
        assert self._run(repo) == []


class TestGuardMessage:
    def test_each_cause_has_its_own_explanation(self):
        msg = _named(_load("_check_gitcrypt_staged.yml"),
                     "Refuse to commit unencrypted git-crypt files"
                     )["ansible.builtin.fail"]["msg"]
        assert "_gitcrypt_inactive" in msg and "_gitcrypt_unmarked" in msg
        assert "file under hosts/ must be" in msg
        assert "git add --renormalize -- hosts" in msg
        assert "canasta_gitattributes_missing_rules" in msg
