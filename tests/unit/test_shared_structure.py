"""Structural tests for task files that every orchestrator uses."""

import os

import pytest
import yaml

from _gitops_flow import flow_source, flow_tasks


REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
GITOPS_TASKS = os.path.join(REPO_ROOT, "roles", "gitops", "tasks")
ORCHESTRATOR_TASKS = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks")


class TestGitopsDispatchers:
    """Each dispatched gitops command has a dispatcher that resolves the
    instance, and a Compose variant that leaves that to the dispatcher."""

    DISPATCHED = ["init", "push", "pull", "diff"]
    # diff on Compose runs only in direct_commands.
    COMPOSE_DISPATCHED = ["init", "push", "pull"]

    def test_dispatcher_files_exist(self):
        for cmd in self.DISPATCHED:
            path = os.path.join(GITOPS_TASKS, "%s.yml" % cmd)
            assert os.path.isfile(path), "Missing dispatcher: %s.yml" % cmd

    def test_compose_variants_exist(self):
        for cmd in self.COMPOSE_DISPATCHED:
            path = os.path.join(GITOPS_TASKS, "%s_compose.yml" % cmd)
            assert os.path.isfile(path), (
                "Missing compose variant: %s_compose.yml" % cmd
            )

    def test_dispatchers_include_resolve_instance(self):
        for cmd in self.DISPATCHED:
            path = os.path.join(GITOPS_TASKS, "%s.yml" % cmd)
            with open(path) as f:
                content = f.read()
            assert "resolve_instance" in content, (
                "Dispatcher %s.yml missing resolve_instance" % cmd
            )

    def test_compose_variants_do_not_resolve_instance(self):
        for cmd in self.COMPOSE_DISPATCHED:
            path = os.path.join(GITOPS_TASKS, "%s_compose.yml" % cmd)
            with open(path) as f:
                content = f.read()
            assert "resolve_instance" not in content, (
                "Compose variant %s_compose.yml should not resolve_instance "
                "(dispatcher handles it)" % cmd
            )


class TestGitopsComposeGitEnv:
    """Every git command in the Compose-side gitops flows must run with
    `environment: "{{ gitops_git_env }}"` so the operator gets:

    - ssh-agent forwarding (via ANSIBLE_SSH_ARGS' ForwardAgent=yes,
      tested separately) chained with this env's GIT_SSH_COMMAND, so
      `git push` on the remote authenticates against private forges.
    - StrictHostKeyChecking=accept-new + UserKnownHostsFile=… so a
      first contact with github.com etc. doesn't kill the play.

    Drift here is invisible at runtime until someone tries to
    onboard a private repo, so the structural check is worth its
    weight in cheap-and-mechanical lines.

    The Kubernetes-side flows generate their own deploy key and
    set GIT_SSH_COMMAND with `-i <key>`; they're allowed to use a
    different env (or no env reference at all).
    """

    # Files on the Compose path that contain at least one git command.
    COMPOSE_FILES = [
        "init_compose.yml",
        "pull_compose.yml",
        "push_compose.yml",
        "join.yml",  # Compose join (k8s join is in join_kubernetes.yml)
    ]

    GIT_VERBS = (
        "git push",
        "git pull",
        "git clone",
        "git fetch",
        "git ls-remote",
    )

    @pytest.mark.parametrize("filename", COMPOSE_FILES)
    def test_every_git_command_has_gitops_git_env(self, filename):
        tasks = flow_tasks(filename)
        # Recursive walk: gitops_*_compose files nest tasks inside
        # block: structures, so we descend into block/rescue/always
        # rather than just iterating the top level.
        offending = []
        for entry in self._walk_tasks(tasks):
            cmd = self._extract_cmd(entry)
            if not cmd:
                continue
            if not any(verb in cmd for verb in self.GIT_VERBS):
                continue
            env = entry.get("environment")
            ok = (
                isinstance(env, str)
                and "gitops_git_env" in env
            ) or (
                isinstance(env, dict)
                and any(
                    "GIT_SSH_COMMAND" in str(v) for v in env.values()
                )
                # The k8s-style override (-i <_ssh_key_path>) doesn't
                # apply on the Compose side; require gitops_git_env.
                and "gitops_git_env" in str(env)
            )
            if not ok:
                offending.append(
                    "%s: task '%s' ran '%s' without gitops_git_env"
                    % (filename, entry.get("name", "<unnamed>"), cmd)
                )
        assert not offending, "\n".join(offending)

    @classmethod
    def _walk_tasks(cls, items):
        """Yield every task dict regardless of nesting depth."""
        if not isinstance(items, list):
            return
        for item in items:
            if not isinstance(item, dict):
                continue
            yield item
            for child_key in ("block", "rescue", "always"):
                if child_key in item:
                    yield from cls._walk_tasks(item[child_key])

    @staticmethod
    def _extract_cmd(task):
        """Pull the cmd string out of an ansible.builtin.command task."""
        for key in ("ansible.builtin.command", "command"):
            mod = task.get(key)
            if isinstance(mod, dict):
                cmd = mod.get("cmd")
                return cmd if isinstance(cmd, str) else ""
            if isinstance(mod, str):
                return mod
        return ""


class TestGitopsReinit:
    """Every init/join entry point must include _reinit_cleanup.yml
    gated on the reinit flag, and surface --reinit in the
    "already initialized" failure message. Drift is silent: the user
    gets stuck with a partial init and no way out, which is exactly
    what --reinit exists to prevent. Cheap to gate against."""

    INIT_JOIN_FILES = [
        "init_compose.yml",
        "join.yml",
    ]

    @pytest.mark.parametrize("filename", INIT_JOIN_FILES)
    def test_includes_reinit_cleanup(self, filename):
        content = flow_source(filename)
        assert "_reinit_cleanup.yml" in content, (
            "%s should include _reinit_cleanup.yml so --reinit can "
            "wipe partial gitops state" % filename
        )
        assert "reinit | default(false) | bool" in content, (
            "%s should gate the cleanup on the reinit flag" % filename
        )

    @pytest.mark.parametrize("filename", INIT_JOIN_FILES)
    def test_already_initialized_message_mentions_reinit(self, filename):
        content = flow_source(filename)
        assert "already initialized" in content
        # The error has to point users at the recovery flag, otherwise
        # the message is a brick wall.
        assert "--reinit" in content, (
            "%s's 'already initialized' message must mention --reinit "
            "so users have a documented recovery path" % filename
        )

    def test_cleanup_file_exists(self):
        assert os.path.isfile(
            os.path.join(GITOPS_TASKS, "_reinit_cleanup.yml")
        )


class TestResolvePasswordHelper:
    """The audit asked for `_resolve_password.yml` to dedupe the
    'use-provided-or-generate' pattern that was repeated three times
    in roles/create/tasks/main.yml. These tests guard against:

    1. The helper getting deleted or moved without updating the call
       sites.
    2. Future password-resolution code in main.yml drifting away from
       the helper and bringing the duplication back.
    """

    HELPER_PATH = os.path.join(
        os.path.dirname(__file__), "..", "..",
        "roles", "create", "tasks", "_resolve_password.yml",
    )
    CREATE_TASKS_DIR = os.path.join(
        os.path.dirname(__file__), "..", "..",
        "roles", "create", "tasks",
    )

    def _read_create_tasks(self):
        """Combined contents of all create-role task files (excluding
        the helper itself). The three password resolutions used to
        live in main.yml; now they live in
        _passwords.yml. The structural assertions don't care which
        file holds them — they just guard against the helper pattern
        being abandoned."""
        combined = []
        for fname in sorted(os.listdir(self.CREATE_TASKS_DIR)):
            if not fname.endswith(".yml"):
                continue
            if fname == "_resolve_password.yml":
                continue  # helper itself; calls generate_password.yml
            with open(os.path.join(self.CREATE_TASKS_DIR, fname)) as f:
                combined.append(f.read())
        return "\n".join(combined)

    def test_helper_exists(self):
        assert os.path.isfile(self.HELPER_PATH)

    def test_helper_sets_resolved_password(self):
        with open(self.HELPER_PATH) as f:
            tasks = yaml.safe_load(f)
        # Walk every task (including those in `block:`) and make sure
        # at least one set_fact targets `_resolved_password` — the
        # contract the call sites depend on.

        def walk(items):
            for t in items or []:
                if not isinstance(t, dict):
                    continue
                yield t
                for k in ("block", "rescue", "always"):
                    if k in t:
                        yield from walk(t[k])
        sets_resolved = []
        for t in walk(tasks):
            sf = t.get("ansible.builtin.set_fact") or t.get("set_fact")
            if isinstance(sf, dict) and "_resolved_password" in sf:
                sets_resolved.append(t.get("name", "<unnamed>"))
        assert sets_resolved, (
            "_resolve_password.yml must set _resolved_password "
            "(both the use-provided and generate branches). Found: %r"
            % sets_resolved
        )

    def test_main_uses_helper_for_each_password(self):
        content = self._read_create_tasks()
        # Three password resolutions: root DB, wiki DB (non-root user
        # branch), admin. Each must include the helper.
        helper_includes = content.count("include_tasks: _resolve_password.yml")
        assert helper_includes >= 3, (
            "Expected at least 3 includes of _resolve_password.yml "
            "(rootdbpass, wikidbpass non-root branch, admin) across "
            "roles/create/tasks/*.yml; found %d" % helper_includes
        )

    def test_main_no_inline_generate_password_for_create_passwords(self):
        """Defensive: nobody should inline-call generate_password.yml
        from the create role for the three passwords the helper now
        owns. If someone adds a new password type that needs the same
        pattern, they should call the helper instead of copy-pasting
        the loop."""
        content = self._read_create_tasks()
        # The helper itself includes generate_password.yml; create-
        # role task files should not directly include it.
        assert "include_tasks:" in content
        inline_generate = content.count("generate_password.yml")
        # 0 is the win; tolerate if some other site legitimately
        # includes it without the helper.
        assert inline_generate <= 0, (
            "Found %d inline generate_password.yml include(s) in "
            "roles/create/tasks/*.yml — these should go through "
            "_resolve_password.yml." % inline_generate
        )


class TestResolveInstanceSkipGate:
    """`instance_lifecycle/start.yml` and `restart.yml` are entered
    both top-level (`canasta start` / `canasta restart`) and from
    nested callers (config set/unset, add, backup restore, devmode
    enable/disable, upgrade) that already passed through
    resolve_instance.yml. The include must be gated on
    `instance_path is not defined` so nested callers skip the
    redundant registry round-trip."""

    LIFECYCLE_FILES = ["start.yml", "restart.yml"]

    @pytest.mark.parametrize("filename", LIFECYCLE_FILES)
    def test_resolve_instance_gated_on_instance_path(self, filename):
        path = os.path.join(
            os.path.dirname(__file__), "..", "..",
            "roles", "instance_lifecycle", "tasks", filename,
        )
        with open(path) as f:
            tasks = yaml.safe_load(f)
        for t in tasks:
            if not isinstance(t, dict):
                continue
            inc = t.get("ansible.builtin.include_tasks") or t.get("include_tasks", "")
            if "resolve_instance.yml" not in str(inc):
                continue
            when = t.get("when", "")
            assert "instance_path is not defined" in when, (
                "%s: resolve_instance.yml include must be gated on "
                "`instance_path is not defined`; got when=%r"
                % (filename, when)
            )
            return
        # If we never found a resolve_instance include, the file
        # changed shape — flag it so reviewers don't lose the test.
        raise AssertionError(
            "%s no longer includes resolve_instance.yml — the gate "
            "test needs updating to reflect the new flow." % filename
        )


class TestPlayLevelDockerHost:
    """canasta.yml's play-level `environment:` exposes DOCKER_HOST so
    every Ansible-routed command's docker / docker compose calls
    inherit it. Operators on rootless podman or rootless Docker
    set this via `canasta create --docker-host=…`.

    Drift here is invisible until someone tries the rootless setup,
    so the structural check is cheap insurance."""

    CANASTA_YML = os.path.join(
        os.path.dirname(__file__), "..", "..", "canasta.yml",
    )

    def test_play_environment_exports_docker_host(self):
        with open(self.CANASTA_YML) as f:
            plays = yaml.safe_load(f)
        # The file has one play.
        play = plays[0]
        env = play.get("environment", {})
        assert "DOCKER_HOST" in env, (
            "canasta.yml must declare a play-level "
            "`environment: { DOCKER_HOST: … }` so docker tasks "
            "honor --docker-host / registry dockerHost."
        )

    def test_docker_host_precedence_flag_then_registry(self):
        # The expression has to:
        #   - prefer the `docker_host` var (from --docker-host on the
        #     current invocation) over the registry value
        #   - fall back to `instance_docker_host` (set by
        #     resolve_instance.yml from the registry) when the flag
        #     wasn't passed
        #   - omit the env var entirely when neither is set, so
        #     Docker keeps using its compiled-in default socket
        with open(self.CANASTA_YML) as f:
            plays = yaml.safe_load(f)
        expr = plays[0]["environment"]["DOCKER_HOST"]
        assert "docker_host" in expr
        assert "instance_docker_host" in expr
        assert "omit" in expr


class TestResilientExec:
    """Long-running remote ops run detached and are polled (resilient_exec)
    so a controller-side SSH reset doesn't abort work still progressing on
    the target."""

    PRIMITIVE = os.path.join(ORCHESTRATOR_TASKS, "resilient_exec.yml")

    def test_primitive_launches_async_and_polls(self):
        tasks = yaml.safe_load(open(self.PRIMITIVE))
        # First task fires the command and returns immediately (poll: 0).
        launch = tasks[0]
        assert launch.get("poll") == 0
        assert launch.get("async") is not None
        assert "ansible.builtin.shell" in launch
        # Completion is polled with async_status in an until-loop that
        # tolerates transient unreachable polls.
        poll = next(t for t in self._walk(tasks) if "until" in t)
        assert "ansible.builtin.async_status" in poll
        assert poll.get("ignore_unreachable") is True

    def test_launch_collapses_only_newlines(self):
        # rx_cmd often comes from a `>-` folded scalar whose more-indented
        # Jinja continuations preserve newlines. Under `shell` (/bin/sh -c) a
        # bare newline is a command separator, so the launch must fold the
        # command onto one line (regression for the helm `--reset-values`
        # rc=127 break) — but collapse ONLY newlines, not all whitespace, so
        # a secret with a tab/double-space on the exec_long path isn't mangled.
        tasks = yaml.safe_load(open(self.PRIMITIVE))
        cmd = tasks[0]["ansible.builtin.shell"]["cmd"]
        assert "regex_replace" in cmd and r"\r\n" in cmd, (
            "launch must collapse newline runs (regex_replace) not all "
            "whitespace, so intra-line spacing in secrets is preserved"
        )
        assert ".split()" not in cmd, (
            "split()|join collapses ALL whitespace and mangles secrets "
            "containing tabs/double-spaces"
        )

    def test_poll_and_cleanup_honor_no_log(self):
        # async_status returns the job's `cmd` and output, so the poll +
        # cleanup tasks must honor rx_no_log.
        tasks = yaml.safe_load(open(self.PRIMITIVE))
        for name in ("Wait for completion", "Clean up async job file"):
            t = next(x for x in self._walk(tasks)
                     if str(x.get("name", "")).startswith(name))
            assert "rx_no_log" in str(t.get("no_log", "")), name

    def test_job_file_cleaned_up_on_every_outcome(self):
        # The job file holds the command and its output, so its removal sits
        # in the `always` of the block that polls and reports failure — a
        # failed or timed-out job is cleaned up too — and a failing cleanup
        # never masks the job's own outcome.
        tasks = yaml.safe_load(open(self.PRIMITIVE))
        guarded = next(t for t in tasks if "block" in t)
        names = [str(t.get("name", "")) for t in guarded["block"]]
        assert any(n.startswith("Wait for completion") for n in names)
        assert any(n.startswith("Report failure") for n in names)
        cleanup = [
            t for t in guarded.get("always", [])
            if (t.get("ansible.builtin.async_status") or {}).get("mode")
            == "cleanup"
        ]
        assert len(cleanup) == 1
        assert cleanup[0].get("ignore_errors") is True
        assert cleanup[0].get("ignore_unreachable") is True
        top_cleanups = [
            t for t in tasks
            if (t.get("ansible.builtin.async_status") or {}).get("mode")
            == "cleanup"
        ]
        assert not top_cleanups

    @staticmethod
    def _walk(tasks):
        for t in tasks or []:
            if not isinstance(t, dict):
                continue
            yield t
            for nested in ("block", "rescue", "always"):
                if nested in t:
                    yield from TestResilientExec._walk(t[nested])

    def test_exec_supports_opt_in_long_mode(self):
        # The shared exec abstraction gains an opt-in long mode (detached +
        # polled) used by update.php / mysqldump callers, while short callers
        # keep the held single connection (gated by exec_long).
        tasks = yaml.safe_load(
            open(os.path.join(ORCHESTRATOR_TASKS, "exec.yml")))
        held = next(t for t in tasks if str(t.get("name", "")).startswith("Execute:"))
        assert "not (exec_long" in str(held.get("when", ""))
        long_task = next(
            t for t in tasks
            if "resilient_exec.yml" in str(
                t.get("ansible.builtin.include_tasks", "")))
        assert "exec_long" in str(long_task.get("when", ""))
