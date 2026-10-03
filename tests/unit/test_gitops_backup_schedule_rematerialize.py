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


def _set_facts(path, key):
    """The set_fact task defining `key`, or None."""
    for task in _tasks(path):
        sf = task.get("ansible.builtin.set_fact") or task.get("set_fact")
        if isinstance(sf, dict) and key in sf:
            return sf
    return None


def test_rematerialize_carries_the_retention_policy_through():
    """config/backup-schedule.yml is where the policy round-trips. If
    rematerialize does not read it back, a gitops pull or a restore
    rewrites the crontab with the purge silently dropped and snapshots
    accumulate from then on."""
    apply = [t for t in _tasks(REMATERIALIZE)
             if "backup_schedule_apply.yml" in _includes(t)]
    assert apply
    assert "retention" in (apply[0].get("vars") or {}), (
        "the re-applied schedule must carry the persisted retention policy"
    )

    read = _set_facts(REMATERIALIZE, "_sched_state_retention")
    assert read is not None, "rematerialize must read the persisted policy"
    assert "retention" in read["_sched_state_retention"]


def test_rematerialize_still_understands_a_legacy_single_duration():
    """Schedules set before the policy became a mapping persisted a lone
    purge_older_than duration; those instances must keep purging."""
    read = _set_facts(REMATERIALIZE, "_sched_state_retention")
    assert "purge_older_than" in read["_sched_state_retention"], (
        "an existing config/backup-schedule.yml carries purge_older_than, "
        "which has always meant --keep-within"
    )
    assert "keep-within" in read["_sched_state_retention"]


def test_set_persists_the_policy_it_was_given():
    persist = os.path.join(
        REPO_ROOT, "roles", "orchestrator", "tasks", "backup_schedule_set.yml")
    copies = [
        (t.get("ansible.builtin.copy") or t.get("copy"))
        for t in _tasks(persist)
        if (t.get("ansible.builtin.copy") or t.get("copy"))
    ]
    assert copies, "set must persist the schedule"
    content = str(copies[0].get("content"))
    assert "retention" in content and "_retention_policy" in content, (
        "the retention policy has to be written to the file, not only to "
        "the crontab, or nothing can re-materialize it"
    )


SCHEDULE_SET = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "backup_schedule_set.yml"
)
SCHEDULE_REMOVE = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "backup_schedule_remove.yml"
)
STAGE_SCHEDULE = os.path.join(
    REPO_ROOT, "roles", "gitops", "tasks", "stage_backup_schedule.yml"
)
GITIGNORE = os.path.join(REPO_ROOT, "roles", "gitops", "files", "gitignore.default")


def _index_of(tasks, pred):
    return next(i for i, t in enumerate(tasks) if pred(t))


def test_schedule_set_stages_the_file_it_writes():
    tasks = _tasks(SCHEDULE_SET)
    write = _index_of(tasks, lambda t: str(
        (t.get("ansible.builtin.copy") or {}).get("dest", "")
    ).endswith("/config/backup-schedule.yml"))
    stage = _index_of(tasks, lambda t: "stage_backup_schedule.yml" in _includes(t))
    assert write < stage


def test_schedule_remove_stages_the_deletion():
    tasks = _tasks(SCHEDULE_REMOVE)
    drop = _index_of(tasks, lambda t: (t.get("ansible.builtin.file") or {}).get(
        "state") == "absent")
    stage = _index_of(tasks, lambda t: "stage_backup_schedule.yml" in _includes(t))
    assert drop < stage


def test_schedule_is_staged_only_on_gitops_instances():
    tasks = _tasks(STAGE_SCHEDULE)
    marker = tasks[0]["ansible.builtin.stat"]["path"]
    assert marker.endswith("/.gitops-host")
    stage = tasks[1]
    assert stage["vars"]["_stage_paths"] == ["config/backup-schedule.yml"]
    assert "stat.exists" in str(stage["when"])


def test_schedule_file_is_not_gitignored(tmp_path):
    import shutil
    import subprocess

    shutil.copy(GITIGNORE, tmp_path / ".gitignore")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    rc = subprocess.run(
        ["git", "check-ignore", "-q", "config/backup-schedule.yml"],
        cwd=tmp_path,
    ).returncode
    assert rc == 1, "config/backup-schedule.yml must be shareable through gitops"
