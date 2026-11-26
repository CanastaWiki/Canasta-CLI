"""The Kubernetes restore records its phases in the restore marker.

k8s_restore_instance.yml marks the files, databases, and secrets phases in
order, each before its own work, through the shared marker tasks.
"""

import os

from test_restore_marker import REPO_ROOT, _index, _named, _phases


RESTORE_K8S = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks", "k8s_restore_instance.yml")


class TestTheKubernetesRestoreMarksItsPhases:
    """The Kubernetes restore is a separate task file, so the Compose
    marks never run there; without its own, an interrupted restore always
    reads 'phase: starting'."""

    def test_its_phases_are_marked_in_order(self):
        assert _phases(RESTORE_K8S) == ["files", "databases", "secrets"]

    def test_each_phase_is_marked_before_its_work(self):
        for mark, work in (
                ("Mark the files phase", "Create restore pod"),
                ("Mark the files phase", "Copy restored config to host"),
                ("Mark the databases phase", "Import each DB dump"),
                ("Mark the secrets phase",
                 "Restore canasta-managed K8s Secrets from snapshot")):
            assert _index(RESTORE_K8S, mark) >= 0, mark
            assert _index(RESTORE_K8S, mark) < _index(RESTORE_K8S, work), \
                (mark, work)

    def test_each_phase_follows_the_previous_ones_work(self):
        assert (_index(RESTORE_K8S, "Restore single wiki public assets to host")
                < _index(RESTORE_K8S, "Mark the databases phase"))
        assert (_index(RESTORE_K8S, "Import each DB dump")
                < _index(RESTORE_K8S, "Mark the secrets phase"))

    def test_the_marks_use_the_marker_tasks(self):
        for phase in ("databases", "secrets"):
            task = _named(RESTORE_K8S, "Mark the %s phase" % phase)
            assert task["ansible.builtin.include_tasks"].strip().endswith(
                "/roles/backup/tasks/_restore_marker.yml")
            assert task["vars"]["restore_marker_phase"] == phase

    def test_the_secrets_phase_is_marked_only_when_there_are_secrets(self):
        task = _named(RESTORE_K8S, "Mark the secrets phase")
        assert task["when"] == "_restore_secrets_filename != ''"
