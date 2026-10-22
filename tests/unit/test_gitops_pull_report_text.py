"""What `canasta gitops pull` prints when it finishes.

The report was built from three ternaries that rendered '' for every
outcome that did not apply, inside a `>-` scalar whose continuation lines
were indented past the block's first line — so YAML kept the newlines
around them. A pull needing nothing printed a trailing space and a blank
line; a pull needing both printed an empty third line.

The outcomes are collected as a list and joined, so each combination is
one sentence, naming what made each outcome necessary.
"""

import ast
import os
import subprocess

import pytest
import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
PULL = os.path.join(REPO_ROOT, "roles", "gitops", "tasks", "pull_compose.yml")


def _find(items, name):
    for t in items or []:
        if not isinstance(t, dict):
            continue
        if t.get("name") == name:
            return t
        for key in ("block", "always", "rescue"):
            if key in t:
                found = _find(t[key], name)
                if found:
                    return found
    return None


def _tasks():
    with open(PULL) as f:
        return yaml.safe_load(f)


def _env():
    jinja2 = pytest.importorskip("jinja2")
    from ansible.plugins.filter.core import FilterModule
    from ansible.plugins.test.core import TestModule

    env = jinja2.Environment()
    env.filters.update(FilterModule().filters())
    env.tests.update(TestModule().tests())
    return env


RESTART = [".env", "config/Caddyfile.site"]
MAINTENANCE = ["extensions/Foo"]


def _report(restart, maintenance, range_base="aaaaaaaaaa",
            prev="aaaaaaaaaa", new="bbbbbbbbbb"):
    env = _env()
    tasks = _tasks()

    summarize = _find(tasks, "Summarize what the pull requires")
    assert summarize is not None, "the summary task is gone or renamed"
    followup = ast.literal_eval(
        env.from_string(
            summarize["ansible.builtin.set_fact"]["_pull_followup"]
        ).render(
            _pull_restart_triggers=RESTART if restart is True else (restart or []),
            _pull_maintenance_triggers=(MAINTENANCE if maintenance is True
                                        else (maintenance or [])),
        ).strip()
    )

    report = _find(tasks, "Report pull results")
    assert report is not None, "the report task is gone or renamed"
    return env.from_string(report["ansible.builtin.debug"]["msg"]).render(
        _pull_prev_commit={"stdout": prev},
        _pull_new_commit={"stdout": new},
        _pull_range_base={"stdout": range_base},
        _pull_followup=followup,
    )


class TestPullReportIsOneSentence:
    def test_the_report_template_has_no_interior_newline(self):
        msg = _find(_tasks(), "Report pull results")["ansible.builtin.debug"]["msg"]
        assert "\n" not in msg.strip(), (
            "the report folds with a newline inside it; keep continuation "
            "lines at the block's own indentation"
        )

    def test_nothing_needed(self):
        assert _report(False, False) == (
            "Pulled aaaaaaa..bbbbbbb. No restart needed.")

    def test_restart_only(self):
        assert _report(True, False) == (
            "Pulled aaaaaaa..bbbbbbb. Restart needed (.env, "
            "config/Caddyfile.site changed).")

    def test_maintenance_only(self):
        assert _report(False, True) == (
            "Pulled aaaaaaa..bbbbbbb. Maintenance update needed "
            "(extensions/Foo changed).")

    def test_both_needed_reads_as_two_sentences(self):
        """The case the ternaries rendered with an empty line between."""
        assert _report(True, True) == (
            "Pulled aaaaaaa..bbbbbbb. Restart needed (.env, "
            "config/Caddyfile.site changed). Maintenance update needed "
            "(extensions/Foo changed).")

    def test_a_long_extension_list_is_shortened(self):
        names = ["extensions/E%d" % i for i in range(8)]
        assert _report(False, names) == (
            "Pulled aaaaaaa..bbbbbbb. Maintenance update needed "
            "(extensions/E0, extensions/E1, extensions/E2, extensions/E3, "
            "extensions/E4 and 3 more changed).")

    @pytest.mark.parametrize("restart,maintenance", [
        (False, False), (True, False), (False, True), (True, True)])
    def test_no_outcome_leaves_stray_whitespace(self, restart, maintenance):
        out = _report(restart, maintenance)
        assert out == out.strip()
        assert "  " not in out


def _git(cwd, *args):
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com",
         "-c", "commit.gpgsign=false", *args],
        cwd=cwd, check=True, capture_output=True, text=True,
    ).stdout.strip()


def _commit(cwd, name):
    with open(os.path.join(cwd, name), "w") as f:
        f.write(name)
    _git(cwd, "add", name)
    _git(cwd, "commit", "-q", "-m", name)


def _range_base(cwd, prev, new):
    env = _env()
    task = _find(_tasks(), "Find where the pulled commits begin")
    assert task is not None, "the range-start task is gone or renamed"
    cmd = env.from_string(task["ansible.builtin.command"]["cmd"]).render(
        _pull_prev_commit={"stdout": prev}, _pull_new_commit={"stdout": new})
    proc = subprocess.run(cmd.split(), cwd=cwd, capture_output=True, text=True)
    return proc.stdout.strip()


@pytest.fixture
def repos(tmp_path):
    remote = tmp_path / "remote.git"
    other = tmp_path / "other"
    host = tmp_path / "host"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    _git(tmp_path, "clone", "-q", str(remote), str(other))
    _commit(other, "base")
    _git(other, "push", "-q", "origin", "HEAD:main")
    _git(tmp_path, "clone", "-q", str(remote), str(host))
    return str(other), str(host)


def _pull_and_report(other, host):
    _commit(other, "remote-change")
    _git(other, "push", "-q", "origin", "HEAD:main")
    prev = _git(host, "rev-parse", "HEAD")
    _git(host, "pull", "-q", "--rebase")
    new = _git(host, "rev-parse", "HEAD")
    base = _range_base(host, prev, new)
    return prev, new, base, _report(False, False, base, prev, new)


class TestPullReportRange:
    def test_without_local_commits_the_range_starts_at_the_old_head(
            self, repos):
        other, host = repos
        prev, new, _, out = _pull_and_report(other, host)
        assert out == "Pulled %s..%s. No restart needed." % (prev[:7], new[:7])

    def test_replayed_local_commits_keep_the_range_in_history(self, repos):
        other, host = repos
        _commit(host, "local-change")
        prev, new, base, out = _pull_and_report(other, host)
        assert not _git(host, "branch", "--contains", prev), (
            "the rebase should have left the pre-pull HEAD off the branch")
        assert out == "Pulled %s..%s. No restart needed." % (base[:7], new[:7])
        assert _git(host, "log", "--format=%s", "%s..%s" % (base, new)) \
            .split() == ["local-change", "remote-change"]

    def test_no_common_ancestor_falls_back_to_the_old_head(self):
        out = _report(False, False, range_base="", prev="cccccccccc")
        assert out == "Pulled ccccccc..bbbbbbb. No restart needed."
