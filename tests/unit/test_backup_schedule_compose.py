"""Regression test for the Compose backup-schedule purge flag.

The original bug: schedule_set.yml generated a host cron calling
`canasta backup purge --keep-within <dur>`, but `canasta backup purge`
did not accept `--keep-within` (it had a canasta-specific `--older-than`
instead). Every scheduled purge failed and snapshots accumulated even
though `create` succeeded.

The fix made `canasta backup purge` mirror restic's `forget` flags.
`schedule set` now mirrors them too, so these tests guard the invariant
directly: every flag the schedule can generate must be one the purge
command accepts. A flag purge does not know kills the scheduled job at
argument parsing, on every run, producing no output at all — the backup
simply stops happening.

Pure YAML-structure parsing, mirroring test_backup_schedule_k8s.py.
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
# The crontab-writing logic lives in the shared apply task (reused by
# schedule set and the restore / gitops-pull rematerialize); schedule set
# itself just persists config/backup-schedule.yml and includes apply.
SCHEDULE_SET = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "backup_schedule_apply.yml",
)
# The CLI flags are collected in the backup role's schedule_set entry,
# which then hands the policy to the shared apply task.
SCHEDULE_ROLE = os.path.join(
    REPO_ROOT, "roles", "backup", "tasks", "schedule_set.yml",
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


def _accepted_flags(command_name):
    """Flags accepted by a command, per command_definitions.yml."""
    with open(COMMAND_DEFS) as f:
        defs = yaml.safe_load(f)
    for cmd in defs.get("commands", []):
        if cmd.get("name") == command_name:
            return {
                "--" + (p.get("long") or p["name"]).replace("_", "-")
                for p in cmd.get("parameters", [])
                if not p.get("positional")
            }
    raise AssertionError("%s not found in command_definitions.yml" % command_name)


def _scheduled_retention_flags():
    """Retention flags `schedule set` can put on the chained purge.

    The scheduler builds them from the parameter names it collects, so
    that list — not a spelling hard-coded into the cron template — is what
    bounds the flags the crontab line can carry.
    """
    with open(SCHEDULE_ROLE) as f:
        tasks = yaml.safe_load(f)
    for task in _walk_tasks(tasks):
        sf = task.get("ansible.builtin.set_fact") or task.get("set_fact")
        if isinstance(sf, dict) and "_retention_params" in sf:
            return {"--" + name.replace("_", "-")
                    for name in sf["_retention_params"]}
    raise AssertionError("schedule_set.yml has no _retention_params set_fact")


class TestComposeSchedulePurgeFlag:
    def test_every_scheduled_retention_flag_is_accepted_by_purge(self):
        emitted = _scheduled_retention_flags()
        accepted = _accepted_flags("backup_purge")
        assert emitted <= accepted, (
            "schedule set would generate `canasta backup purge %s`, which "
            "purge does not accept (it accepts %s)"
            % (sorted(emitted - accepted), sorted(accepted))
        )

    def test_schedule_set_offers_the_full_purge_retention_policy(self):
        """A policy that can be purged by hand must be schedulable —
        otherwise the workaround is a hand-written crontab entry the CLI
        does not own, which `schedule remove` then orphans."""
        purge_retention = {
            f for f in _accepted_flags("backup_purge") if f.startswith("--keep-")
        }
        missing = purge_retention - _accepted_flags("backup_schedule_set")
        assert not missing, (
            "backup purge accepts %s but backup schedule set does not"
            % sorted(missing)
        )

    def test_purge_older_than_survives_as_an_alias(self):
        """In live use with durations from 90d to 180d, so it cannot be
        dropped in favor of --keep-within."""
        assert "--purge-older-than" in _accepted_flags("backup_schedule_set")

    def test_no_retention_policy_chains_no_purge(self):
        """`restic forget` with no policy deletes everything it was not
        told to keep, so an empty flag list must mean no purge at all."""
        with open(SCHEDULE_SET) as f:
            tasks = yaml.safe_load(f)
        for task in _walk_tasks(tasks):
            sf = task.get("ansible.builtin.set_fact") or task.get("set_fact")
            if isinstance(sf, dict) and "_cron_purge" in sf:
                assert "if _cron_retention != '' else ''" in sf["_cron_purge"], (
                    "the purge must be chained only when a retention policy "
                    "was given"
                )
                return
        raise AssertionError("backup_schedule_apply.yml has no _cron_purge")


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


def _cron_command_template():
    """The assembled crontab command line, as a Jinja template string."""
    with open(SCHEDULE_SET) as f:
        tasks = yaml.safe_load(f)
    for task in _walk_tasks(tasks):
        sf = task.get("ansible.builtin.set_fact") or task.get("set_fact")
        if isinstance(sf, dict) and "_cron_cmd" in sf:
            return sf["_cron_cmd"]
    raise AssertionError("backup_schedule_apply.yml has no _cron_cmd set_fact")


class TestScheduledOutputIsLogged:
    """Redirection binds to one simple command, not to an `&&` list. With
    retention configured, `>> backup.log 2>&1` therefore applied to the
    purge alone: a successful run logged only the purge (which reads like
    evidence the backup ran), and a failed one logged nothing at all,
    because `&&` short-circuits. Adding retention silently turned the
    logging off."""

    def test_command_list_is_grouped_before_redirection(self):
        template = _cron_command_template()
        assert "'{ '" in template and "' ; } '" in template, (
            "the create/purge list must be brace-grouped so the redirect "
            "covers both commands, not just the last one")

    def test_redirect_comes_after_the_group(self):
        template = _cron_command_template()
        group_end = template.index("' ; } '")
        log_ref = template.index("_cron_log")
        assert group_end < log_ref, (
            "the redirect has to follow the closing brace, or it applies to "
            "the purge alone again")

    def test_log_redirect_captures_both_streams(self):
        with open(SCHEDULE_SET) as f:
            tasks = yaml.safe_load(f)
        for task in _walk_tasks(tasks):
            sf = task.get("ansible.builtin.set_fact") or task.get("set_fact")
            if isinstance(sf, dict) and "_cron_log" in sf:
                assert "2>&1" in sf["_cron_log"], (
                    "a failure that only writes to stderr must reach the log")
                return
        raise AssertionError("no _cron_log set_fact")
