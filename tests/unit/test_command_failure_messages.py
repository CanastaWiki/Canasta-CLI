"""A failing git checkout in `extension set-version` and a failing `gh pr
create` in PR-mode `gitops push` report what failed instead of Ansible's raw
"non-zero return code" error.

Both run the real tasks in a localhost play against temp repos; gh is a stub
on PATH.
"""

import os
import stat
import subprocess
import sys

import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SET_VERSION = os.path.join(
    REPO_ROOT, "roles", "extensions_skins", "tasks", "set_version.yml")
PUSH_COMPOSE = os.path.join(REPO_ROOT, "roles", "gitops", "tasks", "push_compose.yml")
RAW = "non-zero return code"


def _git(cwd, *args):
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com",
         "-c", "protocol.file.allow=always", *args],
        cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _run_play(tmp_path, tasks, extra_vars, path_prefix=None):
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {"canasta_root": REPO_ROOT,
                 "ansible_python_interpreter": sys.executable, **extra_vars},
        "tasks": tasks,
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")
    if path_prefix:
        env["PATH"] = f"{path_prefix}{os.pathsep}{env['PATH']}"
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    return subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env, cwd=tmp_path)


class TestSetVersionRef:
    def _tasks(self):
        # From the fetch through the checkout; earlier tasks need a container.
        with open(SET_VERSION) as f:
            tasks = yaml.safe_load(f)
        start = next(i for i, t in enumerate(tasks)
                     if t.get("name") == "Fetch the extension's remote")
        end = next(i for i, t in enumerate(tasks)
                   if t.get("name") == "Stage the updated gitlink")
        return tasks[start:end]

    def _setup(self, tmp_path):
        upstream = tmp_path / "upstream"
        upstream.mkdir()
        _git(upstream, "init", "-q", "-b", "master")
        (upstream / "f").write_text("1\n")
        _git(upstream, "add", "f")
        _git(upstream, "commit", "-qm", "one")
        _git(upstream, "tag", "v1")
        (upstream / "f").write_text("2\n")
        _git(upstream, "commit", "-qam", "two")
        _git(upstream, "branch", "REL1_43")
        inst = tmp_path / "inst"
        (inst / "extensions").mkdir(parents=True)
        _git(inst / "extensions", "clone", "-q", str(upstream), "Ext")
        return inst

    def _run(self, tmp_path, inst, ref):
        return _run_play(tmp_path, self._tasks(), {
            "instance_path": str(inst), "_ext_path": "extensions/Ext",
            "extension_name": "Ext", "ref": ref})

    def test_unknown_ref_names_ref_and_leaves_tree_untouched(self, tmp_path):
        inst = self._setup(tmp_path)
        ext = inst / "extensions" / "Ext"
        before = _git(ext, "rev-parse", "HEAD")
        proc = self._run(tmp_path, inst, "nosuchref")
        out = proc.stdout + proc.stderr
        assert proc.returncode != 0, out
        assert "'nosuchref' is not a branch, tag, or commit" in out, out
        assert "extension 'Ext'" in out, out
        assert RAW not in out, out
        assert _git(ext, "rev-parse", "HEAD") == before
        assert _git(ext, "branch", "--show-current") == "master"

    def test_tag_is_checked_out(self, tmp_path):
        inst = self._setup(tmp_path)
        proc = self._run(tmp_path, inst, "v1")
        assert proc.returncode == 0, proc.stdout + proc.stderr
        ext = inst / "extensions" / "Ext"
        assert _git(ext, "rev-parse", "HEAD") == _git(ext, "rev-parse", "v1^{commit}")

    def test_branch_only_on_origin_is_checked_out(self, tmp_path):
        inst = self._setup(tmp_path)
        proc = self._run(tmp_path, inst, "REL1_43")
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert _git(inst / "extensions" / "Ext", "branch", "--show-current") == "REL1_43"

    def test_failed_checkout_reports_git_error(self, tmp_path):
        inst = self._setup(tmp_path)
        ext = inst / "extensions" / "Ext"
        (ext / "f").write_text("local edit\n")
        proc = self._run(tmp_path, inst, "v1")
        out = proc.stdout + proc.stderr
        assert proc.returncode != 0, out
        assert "Could not check out 'v1' in extension 'Ext'" in out, out
        assert RAW not in out, out


class TestPrCreateFailure:
    def _setup(self, tmp_path, gh_stderr):
        remote = tmp_path / "remote.git"
        _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
        inst = tmp_path / "inst"
        inst.mkdir()
        _git(inst, "init", "-q", "-b", "main")
        (inst / "README").write_text("one\n")
        _git(inst, "add", "README")
        _git(inst, "commit", "-qm", "one")
        _git(inst, "remote", "add", "origin", str(remote))
        _git(inst, "push", "-q", "origin", "main")
        (inst / "README").write_text("two\n")
        _git(inst, "add", "README")
        stub = tmp_path / "bin"
        stub.mkdir()
        gh = stub / "gh"
        gh.write_text("#!/bin/sh\nprintf '%s\\n' " + " ".join(
            "'" + line + "'" for line in gh_stderr) + " >&2\nexit 4\n")
        gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
        return inst, remote, stub

    def _run(self, tmp_path, inst, stub):
        with open(PUSH_COMPOSE) as f:
            block = next(t for t in yaml.safe_load(f)
                         if t.get("name") == "Push via pull request")
        return _run_play(tmp_path, [block], {
            "instance_path": str(inst), "_push_pr_mode": True,
            "_push_has_staged": True, "_push_hostname": "h1",
            "gitops_git_env": {}}, path_prefix=str(stub))

    def test_unauthenticated_gh(self, tmp_path):
        inst, remote, stub = self._setup(tmp_path, [
            "To get started with GitHub CLI, please run:  gh auth login",
            "Alternatively, populate the GH_TOKEN environment variable."])
        proc = self._run(tmp_path, inst, stub)
        out = proc.stdout + proc.stderr
        assert proc.returncode != 0, out
        branch = _git(remote, "branch", "--list", "gitops-h1-*").strip(" *")
        assert branch, "the feature branch should have been pushed"
        assert f"Pushed branch '{branch}' to origin" in out, out
        assert "gh reported: To get started with GitHub CLI" in out, out
        assert "Run 'gh auth login', then open" in out, out
        assert f"gh pr create --head {branch}" in out, out
        assert RAW not in out, out
        assert _git(inst, "branch", "--show-current") == "main"

    def test_other_gh_error(self, tmp_path):
        inst, remote, stub = self._setup(tmp_path, [
            "pull request create failed: GraphQL: something broke"])
        proc = self._run(tmp_path, inst, stub)
        out = proc.stdout + proc.stderr
        assert proc.returncode != 0, out
        assert "GraphQL: something broke" in out, out
        assert "gh auth login" not in out, out
        assert "pull request. Open it on GitHub" in out, out
        assert "gh reported: pull request create failed" in out, out
        assert RAW not in out, out
