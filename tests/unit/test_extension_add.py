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
        assert any("git" in c and "add -A" in c and "&&" not in c
                   for c in cmds), "staging must be its own command task"
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


class TestEnableOnce:
    def test_enable_called_without_per_name_loop(self):
        # enable.yml accepts comma-separated names; looping it re-runs
        # update.php once per name.
        for t in _walk(_load(ADD)):
            inc = str(t.get("ansible.builtin.include_tasks") or "")
            if "enable.yml" in inc:
                assert "loop" not in t, (
                    "enable must be called once with all names joined")
