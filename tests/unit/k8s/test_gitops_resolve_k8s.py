"""gitops resolve covers the Kubernetes values-repo pull.

The values-repo pull stops at conflicts for `gitops resolve` instead of
rolling back, skips its pull when the resolve continues, and is marked
changed when the finished rebase is handed to the apply steps.
"""

import os

import pytest

from test_gitops_resolve import RESOLVE, TASKS, _named


class TestStart:
    @pytest.mark.parametrize("name,result", [("pull_kubernetes.yml", "_git_pull")])
    def test_the_pull_stops_at_conflicts_instead_of_rolling_back(self, name, result):
        path = os.path.join(TASKS, name)
        explain = _named(path, "Explain and roll back a failed pull")
        assert "gitops_resolve_mode | default('') != 'start'" in explain["when"]
        stop = _named(path, "Stop at the conflicts for gitops resolve")
        assert stop["ansible.builtin.include_tasks"] == "_resolve_stop.yml"
        assert "gitops_resolve_mode | default('') == 'start'" in stop["when"]
        assert stop["vars"]["_resolve_failure"] == "{{ %s }}" % result


class TestContinue:
    def test_the_pull_skips_its_rebase_when_continuing(self):
        assert "gitops_resolve_mode | default('') != 'continue'" in \
            _named(os.path.join(TASKS, "pull_kubernetes.yml"), "Pull values repo")["when"]

    def test_the_finished_rebase_is_applied_as_the_pull_would(self):
        hand = _named(RESOLVE, "Hand the finished rebase to the pull's apply steps")["ansible.builtin.set_fact"]
        assert hand["_git_pull"]["changed"] is True
