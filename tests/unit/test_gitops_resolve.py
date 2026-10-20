"""canasta gitops resolve: replay a conflicting pull, stop at its conflicts,
and finish or abandon it with canasta commands instead of raw git."""
import os

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TASKS = os.path.join(REPO_ROOT, "roles", "gitops", "tasks")
RESOLVE = os.path.join(TASKS, "resolve.yml")
STOP = os.path.join(TASKS, "_resolve_stop.yml")
REPORT = os.path.join(TASKS, "_resolve_report.yml")
DEFS = os.path.join(REPO_ROOT, "meta", "command_definitions.yml")


def _tasks(path):
    with open(path) as fh:
        return yaml.safe_load(fh) or []


def _named(path, name):
    return next(t for t in _tasks(path) if t.get("name") == name)


def _names(path):
    return [t.get("name") for t in _tasks(path)]


class TestTheCommand:
    def _def(self):
        return next(c for c in _load_defs() if c["name"] == "gitops_resolve")

    def test_it_runs_while_a_pull_is_unfinished(self):
        assert self._def().get("unfinished_pull_ok") is True

    def test_continue_is_the_flag_for_resume(self):
        params = {p["name"]: p for p in self._def()["parameters"]}
        assert params["resume"]["long"] == "continue"
        assert params["keep"]["choices"] == ["local", "remote"]
        assert params["files"]["as_list"] is True

    def test_it_is_in_the_gitops_group(self):
        with open(os.path.join(REPO_ROOT, "canasta.py")) as fh:
            assert '"push", "pull", "resolve",' in fh.read()


def _load_defs():
    with open(DEFS) as fh:
        return yaml.safe_load(fh)["commands"]


class TestStart:
    def test_the_pull_stops_at_conflicts_instead_of_rolling_back(self):
        for name, result in (("pull_compose.yml", "_pull_result"), ("pull_kubernetes.yml", "_git_pull")):
            path = os.path.join(TASKS, name)
            explain = _named(path, "Explain and roll back a failed pull")
            assert "gitops_resolve_mode | default('') != 'start'" in explain["when"]
            stop = _named(path, "Stop at the conflicts for gitops resolve")
            assert stop["ansible.builtin.include_tasks"] == "_resolve_stop.yml"
            assert "gitops_resolve_mode | default('') == 'start'" in stop["when"]
            assert stop["vars"]["_resolve_failure"] == "{{ %s }}" % result

    def test_stopping_reports_then_ends_the_run(self):
        names = _names(STOP)
        assert names.index("Report what is left to resolve") < names.index("End the run at the conflicts")
        assert _named(STOP, "End the run at the conflicts")["ansible.builtin.meta"] == "end_host"

    def test_a_pull_that_never_stopped_has_nothing_to_resolve(self):
        task = _named(STOP, "Report a pull that failed before replaying anything")
        assert "nothing to resolve" in task["ansible.builtin.fail"]["msg"]

    def test_starting_twice_is_refused(self):
        task = _named(RESOLVE, "Refuse to start while a pull is being resolved")
        assert "_resolve_in_progress | bool" in task["when"]


class TestKeep:
    def test_local_is_theirs_and_remote_is_ours_during_a_rebase(self):
        argv = _named(RESOLVE, "Keep one version of each file")["ansible.builtin.command"]["argv"]
        assert "'--theirs' if keep == 'local' else '--ours'" in argv[2]

    def test_only_conflicting_files_can_be_kept(self):
        assert _named(RESOLVE, "Refuse to keep a file that is not conflicting")

    def test_kept_files_are_staged(self):
        argv = _named(RESOLVE, "Mark the kept files resolved")["ansible.builtin.command"]["argv"]
        assert argv.startswith("{{ ['git', 'add', '--']")


class TestContinue:
    def test_leftover_markers_and_unkept_files_refuse(self):
        grep = _named(RESOLVE, "Look for conflict markers left in edited files")
        assert "^(<<<<<<<|>>>>>>>) " in grep["ansible.builtin.command"]["argv"]
        refuse = _named(RESOLVE, "Refuse to finish while conflicts remain")
        assert "_pull_conflict_encrypted + _pull_conflict_binary" in refuse["vars"]["_resolve_one_side"]

    def test_the_start_commit_is_read_before_the_rebase_finishes(self):
        names = _names(RESOLVE)
        assert names.index("Read the commit the pull started from") < names.index("Finish the rebase")

    def test_a_later_conflicting_commit_stops_again(self):
        names = _names(RESOLVE)
        assert names.index("Check whether the rebase stopped again") < \
            names.index("End the run at the next commit's conflicts") < \
            names.index("Apply the resolved pull")

    def test_the_finished_rebase_is_applied_as_the_pull_would(self):
        hand = _named(RESOLVE, "Hand the finished rebase to the pull's apply steps")["ansible.builtin.set_fact"]
        assert hand["_pull_prev_commit"]["stdout"] == "{{ _resolve_orig_head.stdout | trim }}"
        assert hand["_pull_result"]["changed"] is True and hand["_git_pull"]["changed"] is True
        apply = _named(RESOLVE, "Apply the resolved pull")
        assert apply["vars"]["gitops_resolve_mode"] == "continue"

    def test_the_pull_skips_its_rebase_when_continuing(self):
        path = os.path.join(TASKS, "pull_compose.yml")
        for name in ("Check for uncommitted changes to tracked files", "Get current commit hash",
                     "Pull from remote", "Sync wikis.yaml with its gitops template",
                     "Write .env edits into the gitops sources"):
            assert "gitops_resolve_mode | default('') != 'continue'" in str(_named(path, name)["when"]), name
        assert "gitops_resolve_mode | default('') != 'continue'" in \
            _named(os.path.join(TASKS, "pull_kubernetes.yml"), "Pull values repo")["when"]


class TestMessages:
    def test_the_report_says_what_each_file_needs(self):
        msg = _named(REPORT, "Report what is left to resolve")["ansible.builtin.debug"]["msg"]
        assert "edit out the conflict markers" in msg
        assert "keep one version" in msg
        assert "--continue" in msg and "--abort" in msg

    def test_the_guard_points_at_resolve(self):
        with open(os.path.join(REPO_ROOT, "roles", "common", "tasks", "check_unfinished_pull.yml")) as fh:
            body = fh.read()
        assert "canasta gitops resolve -i {{ instance_id }} --continue" in body
