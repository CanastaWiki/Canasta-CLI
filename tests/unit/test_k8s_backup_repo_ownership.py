"""Guard: a K8s local-path restic repo must end up owned by the operator.

restic runs as root in the backup Job (so it can read every backup source), so
a local hostPath repo it writes is root-owned and the operator can't remove it
without sudo. The Job must chown the repo back to its parent directory's owner
after the op, gated on a local-path repo, and preserve restic's exit code so a
failure still surfaces.
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
JOB = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "k8s_run_backup.yml")


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f) or []


def _tasks(path):
    return _load(path)


def _set_facts(tasks):
    out = []
    for t in tasks:
        sf = t.get("ansible.builtin.set_fact") or t.get("set_fact")
        if isinstance(sf, dict):
            out.append((t, sf))
    return out


class TestLocalRepoOwnershipReclaim:
    def test_stats_parent_for_owner(self):
        tasks = _tasks(JOB)
        stat = next(
            (t for t in tasks
             if (t.get("ansible.builtin.stat") or t.get("stat"))
             and "dirname" in str(t.get("ansible.builtin.stat")
                                   or t.get("stat"))),
            None,
        )
        assert stat is not None, (
            "must stat the local repo's parent dir to learn its owner")
        assert "_restic_local_repo" in str(stat.get("when", "")), (
            "the parent stat must be gated on a local-path repo")

    def test_command_chowns_repo_to_parent_owner(self):
        chown = [
            (t, sf) for (t, sf) in _set_facts(_tasks(JOB))
            if "_restic_command" in sf and "chown" in str(sf["_restic_command"])
        ]
        assert chown, "the restic command must chown a local-path repo"
        task, sf = chown[0]
        cmd = str(sf["_restic_command"])
        assert "_restic_repo_parent.stat.uid" in cmd, (
            "chown must target the parent dir's uid (the operator)")
        assert "exit" in cmd and "_rc" in cmd, (
            "the chown must preserve restic's exit code so a failure surfaces")
        assert "_restic_local_repo" in str(task.get("when", "")), (
            "the chown must only run for a local-path repo")

    def test_job_uses_built_command_variable(self):
        # The Job container command must reference the built _restic_command,
        # not a hardcoded restic invocation that would skip the chown.
        text = open(JOB).read()
        assert '- "{{ _restic_command }}"' in text, (
            "the restic container command must use the built _restic_command")
