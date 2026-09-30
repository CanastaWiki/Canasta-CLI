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

    def test_uses_extensions_paths_not_the_bind_mount(self):
        # Include paths go through w/extensions (or w/skins), never the
        # user-extensions mount.
        text = open(ADD).read()
        assert "'user-' ~ _item_dir" not in text
        assert "_item_dir ~ '/\\1/composer.json'" in text

    def test_install_waits_for_the_monitor_link(self):
        exec_cmds = [(t.get("vars") or {}).get("exec_command", "")
                     for t in _walk(_load(ADD))]
        install = [c for c in exec_cmds if "composer update" in c]
        assert install and all('[ ! -e "$f" ]' in c for c in install)

    def test_failed_install_reverts_only_what_this_run_added(self):
        reverts = [t for t in _walk(_load(ADD))
                   if (t.get("canasta_composer_local") or {}).get("state")
                   == "absent"]
        assert reverts
        for t in reverts:
            assert t["canasta_composer_local"]["include"] == (
                "{{ _composer_local.added }}")

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


class TestResolve:
    def test_resolves_on_the_controller(self):
        # The bundled snapshot is under canasta_root on the controller.
        resolve = [t for t in _walk(_load(ADD))
                   if "canasta_extension_resolve" in t]
        assert resolve and all(t.get("delegate_to") == "localhost"
                               for t in resolve)

    def test_overrides_refused_with_several_names(self):
        guard = [t for t in _walk(_load(ADD))
                 if t.get("name") == "Refuse --repository/--branch with several names"]
        assert guard and "ansible.builtin.fail" in guard[0]


class TestFailedSubmoduleAdd:
    def _submodule_block(self):
        return next(t for t in _walk(_load(ADD_ONE))
                    if t.get("name", "").startswith("Add {{ _item_type }} as a gitops submodule"))

    def test_rescue_deletes_clone_and_module_metadata(self):
        block = self._submodule_block()
        assert "submodule add" in _cmd(block["block"][0])
        paths = block["rescue"][0]["loop"]
        assert any("/.git/modules/" in p for p in paths)
        assert any(p.endswith("{{ _item_dir }}/{{ item.name }}")
                   and ".git" not in p for p in paths)

    def test_rescue_still_fails(self):
        rescue = self._submodule_block()["rescue"]
        assert "ansible.builtin.fail" in rescue[-1]

    def test_branch_is_quoted(self):
        for t in _walk(_load(ADD_ONE)):
            cmd = _cmd(t)
            if "-b " in cmd:
                assert "item.branch | quote" in cmd


def _names(tasks):
    return [t.get("name", "") for t in tasks]


def _index(tasks, fragment):
    return next(i for i, n in enumerate(_names(tasks)) if fragment in n)


class TestBundledProbe:
    def _probe(self):
        return next(t for t in _walk(_load(ADD))
                    if t.get("name", "").startswith("Probe bundled"))

    def test_probe_lists_real_directories_not_symlinks(self):
        cmd = self._probe()["block"][0]["vars"]["exec_command"]
        assert "/var/www/mediawiki/w/{{ _item_type }}" in cmd
        assert '[ -d "$d" ]' in cmd and '[ ! -L "$d" ]' in cmd

    def test_probe_failure_fails_instead_of_assuming_not_bundled(self):
        rescue = self._probe()["rescue"]
        assert rescue and "ansible.builtin.fail" in rescue[-1]
        assert not any("set_fact" in str(t) for t in rescue)

    def test_probe_block_is_unconditional(self):
        assert "when" not in self._probe()

    def test_matching_is_case_insensitive(self):
        classify = next(t for t in _walk(_load(ADD))
                        if t.get("name", "").startswith("Classify requested"))
        expr = classify["ansible.builtin.set_fact"]["_classified"]
        assert "item | lower" in expr
        assert "_bundled_map" in expr and "_local_map" in expr
        # Bundled wins over a local checkout of the same name.
        assert expr.index("_bundled_map") < expr.index("_local_map")

    def test_bundled_items_are_not_resolved(self):
        resolve = next(t for t in _walk(_load(ADD))
                       if "canasta_extension_resolve" in t)
        assert resolve["loop"] == "{{ _non_bundled }}"

    def test_overrides_refused_for_bundled(self):
        guard = next(t for t in _walk(_load(ADD))
                     if t.get("name", "").startswith("Refuse --repository/--branch for a bundled"))
        assert "ansible.builtin.fail" in guard
        assert "_bundled_names | length > 0" in guard["when"]

    def test_bundled_items_are_enabled(self):
        for t in _walk(_load(ADD)):
            if "enable.yml" in str(t.get("ansible.builtin.include_tasks") or ""):
                assert "_bundled_names" in t["vars"]["_names"]


class TestCloneGate:
    def _gate(self):
        return next(t for t in _walk(_load(ADD))
                    if t.get("name") == "Refuse to clone without --clone or --repository")

    def test_gate_accepts_clone_or_repository(self):
        when = " ".join(self._gate()["when"])
        assert "_clone_pending | length > 0" in when
        assert "clone | default(false) | bool" in when
        assert "repository | default('', true) | length == 0" in when

    def test_gate_names_url_and_source(self):
        msg = self._gate()["ansible.builtin.fail"]["msg"]
        assert "c.repository" in msg and "c.source_label" in msg
        assert "--clone" in msg

    def test_gate_runs_after_resolve_and_before_any_change(self):
        tasks = _load(ADD)
        gate = _index(tasks, "Refuse to clone without")
        assert _index(tasks, "Resolve {{ _item_type }} git URL") < gate
        assert gate < _index(tasks, "Report bundled")
        assert gate < _index(tasks, "Add each")

    def test_local_checkout_keeps_on_disk_spelling(self):
        collect = next(t for t in _walk(_load(ADD))
                       if t.get("name", "").startswith("Collect resolved"))
        expr = collect["ansible.builtin.set_fact"]["_to_add"]
        assert "item.item.category == 'local'" in expr

    def test_clone_source_is_reported_before_cloning(self):
        tasks = _load(ADD_ONE)
        report = _index(tasks, "Report where")
        assert report < _index(tasks, "Add {{ _item_type }} as a gitops submodule")
        assert report < _index(tasks, "Add {{ _item_type }} as a plain clone")
        msg = tasks[report]["ansible.builtin.debug"]["msg"]
        assert "item.repository" in msg and "item.source_label" in msg
        assert tasks[report]["when"] == "not _add_existing.stat.exists"


class TestCommandDefinitions:
    def _cmd(self, name):
        path = os.path.join(REPO_ROOT, "meta", "command_definitions.yml")
        with open(path) as f:
            data = yaml.safe_load(f)
        return next(c for c in data["commands"] if c["name"] == name)

    def test_clone_flag_defined(self):
        for name in ("extension_add", "skin_add"):
            params = {p["name"]: p for p in self._cmd(name)["parameters"]}
            assert params["clone"]["type"] == "bool"
            assert params["clone"]["default"] is False

    def test_description_explains_the_order(self):
        for name in ("extension_add", "skin_add"):
            text = self._cmd(name)["long_description"]
            assert "Bundled with Canasta" in text
            assert "--clone" in text
            assert "not hosted on" in text
