"""Structural guards for the backup-schedule rematerialize path.

A Compose `gitops pull` must re-materialize the host crontab from the
durable config/backup-schedule.yml the pull brought in, so a schedule
added/edited/removed in the repo actually takes effect on the instance's
host. This path writes a real host crontab, so it is skipped by the CI
integration suite (see run_tests.py) — these structural assertions are the
CI-runnable guard for the wiring instead.

Also pins that `gitops sync` is a Compose no-op (Argo CD only).
"""

import os

import yaml


REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
PULL_COMPOSE = os.path.join(REPO_ROOT, "roles", "gitops", "tasks", "pull_compose.yml")
REMATERIALIZE = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "backup_schedule_rematerialize.yml"
)
TRIGGER_SYNC = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "gitops_trigger_sync.yml"
)


def _tasks(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _includes(task):
    inc = task.get("ansible.builtin.include_tasks") or task.get("include_tasks")
    if isinstance(inc, dict):
        return inc.get("file", "")
    return inc or ""


def test_pull_compose_rematerializes_when_schedule_file_changed():
    rematerialize = [
        t for t in _tasks(PULL_COMPOSE)
        if "backup_schedule_rematerialize.yml" in _includes(t)
    ]
    assert rematerialize, (
        "pull_compose.yml must re-materialize the backup schedule after a pull"
    )
    # Only when the pull actually touched the schedule file.
    assert "config/backup-schedule.yml" in str(rematerialize[0].get("when", "")), (
        "the rematerialize must be gated on config/backup-schedule.yml changing"
    )


def test_rematerialize_applies_when_present_and_unapplies_when_absent():
    tasks = _tasks(REMATERIALIZE)
    apply = [t for t in tasks if "backup_schedule_apply.yml" in _includes(t)]
    unapply = [t for t in tasks if "backup_schedule_unapply.yml" in _includes(t)]
    assert apply, "must apply the schedule when one is persisted"
    assert unapply, "must remove the crontab entry when no schedule is persisted"
    # Apply only when the persisted file has a non-empty cron expression.
    assert "cron_expression" in str(apply[0].get("when", "")), (
        "apply must be gated on a non-empty cron_expression"
    )
    # Unapply only when the file is gone.
    assert "not _sched_state.stat.exists" in str(unapply[0].get("when", "")), (
        "unapply must be gated on the schedule file being absent"
    )


def test_gitops_sync_is_a_compose_noop():
    tasks = _tasks(TRIGGER_SYNC)
    # On Compose, the only action is a debug message gated on the compose
    # orchestrator — no reconciler to trigger.
    debug_compose = [
        t for t in tasks
        if "ansible.builtin.debug" in t and "== 'compose'" in str(t.get("when", ""))
    ]
    assert debug_compose, "gitops sync must be a no-op (debug only) on Compose"
    # The Argo CD sync is gated on the kubernetes orchestrator.
    argocd = [t for t in tasks if "k8s_argocd_sync.yml" in _includes(t)]
    assert argocd and "kubernetes" in str(argocd[0].get("when", "")), (
        "gitops sync must dispatch to Argo CD sync only on Kubernetes"
    )
