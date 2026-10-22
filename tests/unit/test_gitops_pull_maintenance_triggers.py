"""When `gitops pull` recommends a maintenance update, and against what.

update.php is needed when the set or version of extensions and skins
changes, or a settings.yaml loads a different set: not for every file under
extensions/. The changes are read from the commit the instance ran before
the pull's first attempt (recorded in the baseline), not from
.gitops-applied, which can be older when commits reached the checkout
without a pull; a re-apply over commits the instance already runs then
recommends nothing.
"""

import os
import subprocess
import sys

import pytest
import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PULL = os.path.join(REPO_ROOT, "roles", "gitops", "tasks", "pull_compose.yml")
sys.path.insert(0, os.path.join(REPO_ROOT, "filter_plugins"))

from canasta_gitops import canasta_maintenance_triggers  # noqa: E402


def _tasks():
    with open(PULL) as f:
        return yaml.safe_load(f)


def _task(name):
    return next(t for t in _tasks() if t.get("name") == name)


def _git(repo, *args):
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com",
         "-c", "commit.gpgsign=false", *args],
        cwd=repo, check=True, capture_output=True, text=True).stdout


def _commit(repo, files=(), gitlinks=(), remove=()):
    for path, text in files:
        full = repo / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(text)
        _git(repo, "add", path)
    for path, sha in gitlinks:
        _git(repo, "update-index", "--add", "--cacheinfo",
             "160000,%s,%s" % (sha, path))
    for path in remove:
        _git(repo, "rm", "-q", "--cached", "-r", path)
    _git(repo, "commit", "-q", "--allow-empty", "-m", "c")
    return _git(repo, "rev-parse", "HEAD").strip()


def _triggers(repo, base):
    """Run the pull's diff command and feed it to the filter."""
    argv = _task("List the extension and skin changes since the instance's "
                 "commit")["ansible.builtin.command"]["argv"]
    argv = [base if a == "{{ _pull_ran_commit }}" else a for a in argv]
    assert base in argv
    out = subprocess.run(argv, cwd=repo, check=True, capture_output=True,
                         text=True).stdout
    return canasta_maintenance_triggers(out.splitlines())


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q")
    _commit(tmp_path, files=[
        ("extensions/Foo/extension.json", '{"version": "1"}\n'),
        ("extensions/Foo/Foo.settings.php", "<?php\n"),
        ("config/settings/global/settings.yaml", "extensions: []\n"),
    ])
    return tmp_path


SHA_A = "1" * 40
SHA_B = "2" * 40


class TestMaintenanceTriggers:
    def test_a_settings_php_change_needs_no_update(self, repo):
        base = _git(repo, "rev-parse", "HEAD").strip()
        _commit(repo, files=[("extensions/Foo/Foo.settings.php", "<?php // x\n")])
        assert _triggers(repo, base) == []

    def test_a_new_vendored_extension_needs_an_update(self, repo):
        base = _git(repo, "rev-parse", "HEAD").strip()
        _commit(repo, files=[("extensions/Bar/extension.json", "{}\n"),
                             ("extensions/Bar/Bar.php", "<?php\n")])
        assert _triggers(repo, base) == ["extensions/Bar"]

    def test_an_extension_version_change_needs_an_update(self, repo):
        base = _git(repo, "rev-parse", "HEAD").strip()
        _commit(repo, files=[("extensions/Foo/extension.json",
                              '{"version": "2"}\n')])
        assert _triggers(repo, base) == ["extensions/Foo"]

    def test_a_schema_file_change_needs_an_update(self, repo):
        base = _git(repo, "rev-parse", "HEAD").strip()
        _commit(repo, files=[("extensions/Foo/sql/tables.json", "[]\n")])
        assert _triggers(repo, base) == ["extensions/Foo"]

    def test_submodule_added_bumped_and_removed_need_an_update(self, repo):
        base = _git(repo, "rev-parse", "HEAD").strip()
        _commit(repo, gitlinks=[("skins/Baz", SHA_A)])
        assert _triggers(repo, base) == ["skins/Baz"]
        added = _git(repo, "rev-parse", "HEAD").strip()
        _commit(repo, gitlinks=[("skins/Baz", SHA_B)])
        assert _triggers(repo, added) == ["skins/Baz"]
        bumped = _git(repo, "rev-parse", "HEAD").strip()
        _commit(repo, remove=["skins/Baz"])
        assert _triggers(repo, bumped) == ["skins/Baz"]

    def test_a_load_list_change_needs_an_update(self, repo):
        base = _git(repo, "rev-parse", "HEAD").strip()
        _commit(repo, files=[
            ("config/settings/global/settings.yaml", "extensions: [Cite]\n"),
            ("config/settings/wikis/docs/settings.yaml", "skins: [Timeless]\n"),
            ("config/settings/global/Other.php", "<?php\n"),
        ])
        assert _triggers(repo, base) == [
            "config/settings/global/settings.yaml",
            "config/settings/wikis/docs/settings.yaml",
        ]

    def test_a_reapply_from_the_running_commit_needs_no_update(self, repo):
        """The stale range holds a new extension, but the instance's commit
        is HEAD, so the pull itself brings none."""
        _commit(repo, files=[("extensions/Bar/extension.json", "{}\n")])
        head = _git(repo, "rev-parse", "HEAD").strip()
        assert _triggers(repo, head) == []


class TestDiffBase:
    def _ran_commit(self, baseline, exists_rc=0, diff_base="applied"):
        jinja2 = pytest.importorskip("jinja2")
        expr = _task("Choose the commit the instance ran before this pull")[
            "ansible.builtin.set_fact"]["_pull_ran_commit"]
        return jinja2.Environment().from_string(expr).render(
            _pull_baseline=baseline,
            _pull_baseline_commit_exists={"rc": exists_rc},
            _pull_diff_base=diff_base,
        ).strip()

    def test_the_baseline_commit_is_preferred(self):
        assert self._ran_commit({"commit": "pre"}) == "pre"

    def test_a_baseline_without_a_commit_falls_back(self):
        assert self._ran_commit({"env": "e"}) == "applied"

    def test_a_vanished_baseline_commit_falls_back(self):
        assert self._ran_commit({"commit": "gone"}, exists_rc=1) == "applied"
