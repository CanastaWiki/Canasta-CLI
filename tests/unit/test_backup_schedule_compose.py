"""Regression test for the Compose backup-schedule purge flag.

The original bug: schedule_set.yml generated a host cron calling
`canasta backup purge --keep-within <dur>`, but `canasta backup purge`
did not accept `--keep-within` (it had a canasta-specific `--older-than`
instead). Every scheduled purge failed and snapshots accumulated even
though `create` succeeded.

The fix made `canasta backup purge` mirror restic's `forget` flags
(including `--keep-within`). These tests guard the invariant directly:
the flag the schedule generates must be one the purge command accepts.

Pure YAML-structure parsing, mirroring test_backup_schedule_k8s.py.
"""

import os
import re

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
# The crontab-writing logic lives in the shared apply task (reused by
# schedule set and the restore / gitops-pull rematerialize); schedule set
# itself just persists config/backup-schedule.yml and includes apply.
SCHEDULE_SET = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "backup_schedule_apply.yml",
)
COMMAND_DEFS = os.path.join(REPO_ROOT, "meta", "command_definitions.yml")


def _walk_tasks(tasks):
    """Yield every task dict, descending into block/rescue/always."""
    for t in tasks or []:
        if not isinstance(t, dict):
            continue
        yield t
        for nested in ("block", "rescue", "always"):
            if nested in t:
                yield from _walk_tasks(t[nested])


def _compose_purge_flag():
    """The retention flag in the Compose `_cron_purge` template."""
    with open(SCHEDULE_SET) as f:
        tasks = yaml.safe_load(f)
    for task in _walk_tasks(tasks):
        sf = task.get("ansible.builtin.set_fact") or task.get("set_fact")
        if isinstance(sf, dict) and "_cron_purge" in sf:
            m = re.search(r"backup purge\b.*?(--[a-z-]+)", sf["_cron_purge"])
            assert m, "no purge flag found in _cron_purge template"
            return m.group(1)
    raise AssertionError("schedule_set.yml has no _cron_purge set_fact")


def _purge_accepted_flags():
    """Flags accepted by `canasta backup purge`, per command_definitions."""
    with open(COMMAND_DEFS) as f:
        defs = yaml.safe_load(f)
    for cmd in defs.get("commands", []):
        if cmd.get("name") == "backup_purge":
            return {
                "--" + p["name"].replace("_", "-")
                for p in cmd.get("parameters", [])
            }
    raise AssertionError("backup_purge not found in command_definitions.yml")


class TestComposeSchedulePurgeFlag:
    def test_schedule_purge_flag_is_accepted_by_purge(self):
        flag = _compose_purge_flag()
        accepted = _purge_accepted_flags()
        assert flag in accepted, (
            f"schedule generates `canasta backup purge {flag}` but purge "
            f"only accepts {sorted(accepted)}"
        )

    def test_schedule_uses_restic_native_keep_within(self):
        assert _compose_purge_flag() == "--keep-within"


def _compose_tasks():
    """Every task in schedule_set.yml. Scheduling is now orchestrator-
    agnostic host-crontab logic (no Compose/K8s branch), so this is just
    the whole file."""
    with open(SCHEDULE_SET) as f:
        tasks = yaml.safe_load(f)
    return list(_walk_tasks(tasks))


class TestComposeScheduleCanastaResolution:
    """The local (no --host) path must not gate on probing the host for a
    'canasta' executable — canasta is necessarily installed locally (it's
    running now). The probe + fail belongs only to the remote path."""

    def test_no_unconditional_local_canasta_probe(self):
        for task in _compose_tasks():
            shell = task.get("ansible.builtin.shell") or task.get("shell")
            if shell and "command -v canasta" in str(shell):
                # The probe must be skipped for local targets (it fails
                # inside the canasta-docker container, which has no
                # 'canasta' on PATH).
                when = task.get("when")
                when_text = " ".join(when) if isinstance(when, list) else str(when)
                assert "_sched_is_local" in when_text, (
                    "local schedule set must not probe the host for canasta"
                )

    def test_cron_command_uses_wrapper_recorded_path(self):
        for task in _compose_tasks():
            sf = task.get("ansible.builtin.set_fact") or task.get("set_fact")
            if isinstance(sf, dict) and "_canasta_bin" in sf:
                assert "canasta_cli_bin" in sf["_canasta_bin"], (
                    "local cron must reuse the wrapper's canasta_cli_bin path"
                )
                return
        raise AssertionError("schedule_set.yml has no _canasta_bin set_fact")

    def test_containerized_local_writes_host_crontab_file(self):
        """The containerized CLI can't reach the live host crontab, so it
        edits the wrapper-mounted file (CANASTA_HOST_CRONTAB) via
        blockinfile instead of the cron module."""
        blockinfile = [
            (t.get("ansible.builtin.blockinfile") or t.get("blockinfile"))
            for t in _compose_tasks()
            if (t.get("ansible.builtin.blockinfile") or t.get("blockinfile"))
        ]
        assert blockinfile, "no blockinfile task for the host crontab file"
        assert "_host_crontab_file" in str(blockinfile[0].get("path")), (
            "blockinfile must target the wrapper-provided host crontab file"
        )

    def test_native_and_host_file_paths_are_mutually_exclusive(self):
        """The cron module (native/remote) and blockinfile (local
        containerized) paths are gated on the same _sched_use_file flag,
        so exactly one runs."""
        cron = [t for t in _compose_tasks()
                if (t.get("ansible.builtin.cron") or t.get("cron"))]
        bf = [t for t in _compose_tasks()
              if (t.get("ansible.builtin.blockinfile") or t.get("blockinfile"))]
        assert cron and bf
        assert str(cron[0].get("when")).strip() == "not _sched_use_file"
        assert str(bf[0].get("when")).strip() == "_sched_use_file"

    def test_host_file_path_requires_local_target(self):
        """The local classification must consider the instance's registry
        host, not merely the absence of --host — resolve_instance switches
        the connection to a registry-pinned remote host even without
        --host."""
        for task in _compose_tasks():
            sf = task.get("ansible.builtin.set_fact") or task.get("set_fact")
            if isinstance(sf, dict) and "_sched_is_local" in sf:
                assert "_instance_host" in sf["_sched_is_local"], (
                    "local classification must consider _instance_host"
                )
                return
        raise AssertionError("no _sched_is_local classification found")


def _tasks_of(path):
    with open(path) as f:
        return list(_walk_tasks(yaml.safe_load(f)))


def _inc(t):
    return str(t.get("ansible.builtin.include_tasks") or t.get("include_tasks") or "")


class TestSchedulePersistence:
    """The schedule is durable instance state: set persists
    config/backup-schedule.yml then applies; remove drops it then unapplies;
    restore and gitops pull re-materialize the crontab from the file."""

    ROLES = os.path.join(REPO_ROOT, "roles")
    SET = os.path.join(ROLES, "orchestrator", "tasks", "backup_schedule_set.yml")
    REMOVE = os.path.join(ROLES, "orchestrator", "tasks", "backup_schedule_remove.yml")
    REMAT = os.path.join(
        ROLES, "orchestrator", "tasks", "backup_schedule_rematerialize.yml")
    RESTORE = os.path.join(ROLES, "backup", "tasks", "restore.yml")
    PULL_COMPOSE = os.path.join(ROLES, "gitops", "tasks", "pull_compose.yml")

    def test_set_persists_file_then_applies(self):
        tasks = _tasks_of(self.SET)
        copies = [(t.get("ansible.builtin.copy") or t.get("copy")) for t in tasks
                  if (t.get("ansible.builtin.copy") or t.get("copy"))]
        assert any("config/backup-schedule.yml" in str(c.get("dest")) for c in copies), \
            "set must persist config/backup-schedule.yml"
        assert any("backup_schedule_apply.yml" in _inc(t) for t in tasks), \
            "set must include the apply task"

    def test_remove_drops_file_then_unapplies(self):
        tasks = _tasks_of(self.REMOVE)
        files = [(t.get("ansible.builtin.file") or t.get("file")) for t in tasks
                 if (t.get("ansible.builtin.file") or t.get("file"))]
        assert any("config/backup-schedule.yml" in str(f.get("path"))
                   and f.get("state") == "absent" for f in files), \
            "remove must delete config/backup-schedule.yml"
        assert any("backup_schedule_unapply.yml" in _inc(t) for t in tasks)

    def test_rematerialize_reads_file_and_branches(self):
        tasks = _tasks_of(self.REMAT)
        assert any((t.get("ansible.builtin.slurp") or t.get("slurp")) for t in tasks), \
            "rematerialize must read the persisted file"
        incs = [_inc(t) for t in tasks]
        assert any("backup_schedule_apply.yml" in i for i in incs), "apply when present"
        assert any("backup_schedule_unapply.yml" in i for i in incs), "unapply when absent"

    def test_restore_rematerializes(self):
        assert any("backup_schedule_rematerialize.yml" in _inc(t)
                   for t in _tasks_of(self.RESTORE)), \
            "restore must re-materialize the schedule from restored state"

    def test_gitops_pull_compose_rematerializes_when_schedule_changed(self):
        remat = [t for t in _tasks_of(self.PULL_COMPOSE)
                 if "backup_schedule_rematerialize.yml" in _inc(t)]
        assert remat, "pull_compose must re-materialize the schedule"
        assert "config/backup-schedule.yml" in str(remat[0].get("when")), \
            "rematerialize must be gated on the schedule file changing"
