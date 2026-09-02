"""Guards for `canasta extension|skin add` task structure: Kubernetes
instances must be refused, the gitops commit must be split from staging,
the version probe must not scan the filesystem, and a failed detection
must fail loudly.
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
TASKS = os.path.join(
    REPO_ROOT, "roles", "extensions_skins", "tasks")
ADD = os.path.join(TASKS, "add.yml")
ADD_ONE = os.path.join(TASKS, "_add_one.yml")


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f) or []


def _walk(tasks):
    for t in tasks or []:
        if not isinstance(t, dict):
            continue
        yield t
        for nested in ("block", "rescue", "always"):
            if nested in t:
                yield from _walk(t[nested])


def _cmd(t):
    c = t.get("ansible.builtin.command") or t.get("command") or {}
    return c.get("cmd", "") if isinstance(c, dict) else str(c)


def _current_branch_probe(t):
    # The task that records the existing checkout's branch. Registering it in
    # the `register:` key marks it as the probe rather than a re-align fetch
    # or checkout.
    return t.get("register") == "_current_branch"


class TestKubernetesGuard:
    def test_guard_uses_instance_orchestrator(self):
        # 'orchestrator' is never set by resolve_instance; the guard must
        # read 'instance_orchestrator' or it never fires.
        text = open(ADD).read()
        assert "instance_orchestrator | default('compose') in ['kubernetes', 'k8s']" in text, (
            "add.yml must refuse kubernetes/k8s via instance_orchestrator")

    def test_restart_skipped_on_kubernetes(self):
        # The Compose restart must not run on a Kubernetes instance.
        for t in _walk(_load(ADD)):
            start = t.get("ansible.builtin.include_role") or {}
            if isinstance(start, dict) and start.get("tasks_from") == "start.yml":
                assert "not in ['kubernetes', 'k8s']" in str(t.get("when")), (
                    "the orchestrator restart must be guarded against "
                    "kubernetes instances")


class TestGitopsCommit:
    def test_no_chained_commands(self):
        # 'command' does not invoke a shell, so '&&' chains reach git as
        # pathspecs. Staging and committing must be separate tasks.
        for t in _walk(_load(ADD_ONE)):
            assert "&&" not in _cmd(t), (
                "command tasks must not chain commands with &&")

    def test_stage_and_commit_are_separate_tasks(self):
        cmds = [_cmd(t) for t in _walk(_load(ADD_ONE)) if _cmd(t)]
        # Staging must be its own task and stage only .gitmodules + the item
        # path. A bare `git add -A` would sweep unrelated working-tree state
        # and bypass the git-crypt guard that protects hosts/**/vars.yaml.
        assert any("git" in c and "add" in c and "gitmodules" in c
                   and "add -A" not in c
                   for c in cmds), (
            "staging must stage .gitmodules explicitly, not 'git add -A'")
        assert any("commit.gpgsign=false commit" in c for c in cmds), (
            "committing must be its own command task")


class TestGitUrlHandling:
    def test_git_urls_after_dashdash(self):
        # Repository URLs come from a community dataset; '--' stops git from
        # parsing them as options.
        cmds = [_cmd(t) for t in _walk(_load(ADD_ONE)) if _cmd(t)]
        clone_like = [c for c in cmds
                      if "submodule add" in c or "clone" in c]
        assert len(clone_like) >= 2
        for c in clone_like:
            assert "--" in c and "{{ item.repository" in c.split("--", 1)[1], (
                "repository URL must follow a bare '--'")

    def test_branch_probe_is_detached_head_safe(self):
        # gitops paths (git submodule update --init --recursive) check the
        # submodule out on a detached HEAD. `git symbolic-ref --short HEAD`
        # exits 128 there, which would silently skip both the re-align and the
        # warning. `git rev-parse --abbrev-ref HEAD` prints 'HEAD' with rc 0
        # when detached and the branch name otherwise, so the probe must use it.
        probes = [t for t in _walk(_load(ADD_ONE))
                  if _current_branch_probe(t)]
        assert len(probes) == 1, "exactly one branch probe should exist"
        cmd = _cmd(probes[0])
        assert "symbolic-ref" not in cmd, (
            "branch probe must not use symbolic-ref (fails on detached HEAD)")
        assert "rev-parse --abbrev-ref HEAD" in cmd, (
            "branch probe must use rev-parse --abbrev-ref HEAD so it "
            "survives a detached HEAD (gitops submodules)")

    def test_probe_failure_has_own_message(self):
        # When the probe itself fails (not a git repo, rc != 0) it must say so
        # instead of leaving the rc != 0 case in a silent gap between the
        # re-align (gated on rc == 0) and the warn (gated on rc == 0).
        warns = [t for t in _walk(_load(ADD_ONE))
                 if (t.get("ansible.builtin.debug") or {}).get("msg")]
        msg_tasks = [t for t in warns
                     if "could not determine" in
                        str((t.get("ansible.builtin.debug") or {}).get("msg"))
                        .lower()]
        assert msg_tasks, (
            "there must be a debug task messaging a probe failure")
        when = str(msg_tasks[0].get("when") or "")
        assert "_current_branch.rc" in when and "not" in when, (
            "the probe-failure message must be gated on rc != 0")

    def test_probe_failure_names_non_git_directory(self):
        # rc != 0 is usually "not a git repository" (a manually copied-in
        # directory), so the warning must present that diagnosis first —
        # advising only --branch/--mw-version is the wrong advice there.
        warns = [t for t in _walk(_load(ADD_ONE))
                 if (t.get("ansible.builtin.debug") or {}).get("msg")]
        msg_tasks = [t for t in warns
                     if "could not determine" in
                        str((t.get("ansible.builtin.debug") or {}).get("msg"))
                        .lower()]
        msg = str(msg_tasks[0]["ansible.builtin.debug"]["msg"]).lower()
        assert "not a git checkout" in msg, (
            "the probe-failure warning must name the non-git-directory case")

    def test_detached_pin_is_preserved(self):
        # A detached HEAD at a commit that is an ancestor of the expected
        # branch tip is how `extension set-version --ref` (and gitops
        # submodule checkouts) leave the submodule — a deliberate pin. `add`
        # must classify it via merge-base --is-ancestor and must not move it.
        tasks = list(_walk(_load(ADD_ONE)))
        assert any("merge-base --is-ancestor" in _cmd(t) for t in tasks), (
            "detached-checkout classification must use merge-base --is-ancestor")
        realigns = [t for t in tasks
                    if t.get("block") is not None
                    and "Re-align" in str(t.get("name", ""))]
        assert realigns, "the re-align block must exist"
        when = str(realigns[0].get("when"))
        assert "_detached_is_pin" in when and "not" in when, (
            "the re-align must not fire when the detached commit is a "
            "deliberate pin")

    def test_realign_reports_commit_range(self):
        # The re-align notification must name the commits it moved between
        # (old and new short SHAs) so the change is visible and revertible
        # from the command output alone.
        tasks = list(_walk(_load(ADD_ONE)))
        notifies = [t for t in tasks
                    if (t.get("ansible.builtin.debug") or {}).get("msg")
                    and "switched to expected" in
                    str(t["ansible.builtin.debug"]["msg"]).lower()]
        assert notifies, "there must be a re-aligned notification"
        msg = str(notifies[0]["ansible.builtin.debug"]["msg"])
        assert "_pre_switch_sha" in msg and "_post_switch_sha" in msg, (
            "the re-aligned notification must include the pre/post-switch SHAs")


class TestVersionDetection:
    def test_probe_uses_canonical_path(self):
        exec_cmds = [(t.get("vars") or {}).get("exec_command", "")
                     for t in _walk(_load(ADD))
                     if (t.get("vars") or {}).get("exec_command")]
        assert any("/var/www/mediawiki/w/maintenance/version.php" in c
                   for c in exec_cmds), (
            "version detection must use /var/www/mediawiki/w directly")

    def test_probe_never_scans_filesystem(self):
        text = open(ADD).read()
        assert "find /" not in text and "find /var/www" not in text, (
            "version detection must not find(1)-scan the container")

    def test_failed_detection_fails_loudly(self):
        fails = [t for t in _walk(_load(ADD))
                 if "ansible.builtin.fail" in t or "fail" in t]
        assert any("--mw-version" in str(f.get("ansible.builtin.fail") or f)
                   for f in fails), (
            "undetected MediaWiki version must abort with guidance to pass "
            "--mw-version, not silently fall back to the default branch")

    def test_malformed_version_fails_loudly(self):
        # A regex that only gates on emptiness re-trusts a probing artifact
        # (e.g. 'MW_VERSION 1.44' passed through by sed when the Defines.php
        # fallback finds no three-component version). A non-1.x string must
        # fail loudly too, or mw_minor_to_rel() returns None and the add
        # silently proceeds on the default branch.
        fail_tasks = [t for t in _walk(_load(ADD))
                      if "ansible.builtin.fail" in t]
        guard = [t for t in fail_tasks
                 if "--mw-version" in str(t.get("ansible.builtin.fail")
                                           or t.get("fail") or "")]
        assert guard, "the version guard task should exist"
        when = str(guard[0].get("when") or "")
        assert "_mw_version" in when and "is not match" in when, (
            "version guard must match _mw_version against the 1.x form, "
            "not only test for emptiness")
        assert "1\\\\." in when and "\\\\d" in when, (
            "the guard must use the 1.x version regex ^1\\.\\d+(\\.\\d+)?$")

    def test_probe_falls_back_to_defines_php(self):
        # Some images ship no maintenance/version.php; MW_VERSION in
        # includes/Defines.php is always present and must be the fallback.
        exec_cmds = [(t.get("vars") or {}).get("exec_command", "")
                     for t in _walk(_load(ADD))
                     if (t.get("vars") or {}).get("exec_command")]
        assert any("includes/Defines.php" in c and "MW_VERSION" in c
                   for c in exec_cmds), (
            "version detection must fall back to MW_VERSION in "
            "includes/Defines.php when version.php is absent")


class TestComposerRequirements:
    def test_registers_composer_local_json(self, tmp_dir=None):
        text = open(ADD).read()
        assert "canasta_composer_local" in text, (
            "items shipping a composer.json must be registered in "
            "config/composer.local.json")

    def test_uses_bind_mount_paths(self):
        # instance_path/extensions maps to w/user-extensions; the
        # w/extensions symlink does not exist until the next container
        # start, so include paths must use the user- prefixed mount.
        text = open(ADD).read()
        assert "'user-' ~ _item_dir" in text, (
            "composer.local.json entries must reference the "
            "user-extensions/user-skins bind mount")

    def test_runs_composer_update_no_dev(self):
        exec_cmds = [(t.get("vars") or {}).get("exec_command", "")
                     for t in _walk(_load(ADD))
                     if (t.get("vars") or {}).get("exec_command")]
        assert any("composer update --no-dev" in c for c in exec_cmds), (
            "non-dev composer requirements must be installed explicitly "
            "(the image's boot-time update tolerates failure silently)")

    def test_gitops_commit_is_scoped_to_the_file(self):
        cmds = [_cmd(t) for t in _walk(_load(ADD)) if _cmd(t)]
        assert any("add -- config/composer.local.json" in c for c in cmds), (
            "the gitops staging commit must be scoped to "
            "config/composer.local.json")


class TestEnableOnce:
    def test_enable_called_without_per_name_loop(self):
        # enable.yml accepts comma-separated names; looping it re-runs
        # update.php once per name.
        for t in _walk(_load(ADD)):
            inc = str(t.get("ansible.builtin.include_tasks") or "")
            if "enable.yml" in inc:
                assert "loop" not in t, (
                    "enable must be called once with all names joined")
