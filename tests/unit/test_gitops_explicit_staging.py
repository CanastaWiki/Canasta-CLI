"""Guard: gitops flows that stage on their own stage an explicit path list.

gitops init (Compose and Kubernetes) and Kubernetes push commit without the
operator choosing files. A whole-tree `git add -A` there sweeps in anything
lying in the instance directory — editor backups of .env, stray dumps, scratch
files — and pushes it to the remote. These flows must stage only the
gitops-managed paths, while still recording deletions within them.
"""

import os
import shlex
import shutil
import subprocess

import pytest
import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
TASKS_DIR = os.path.join(REPO_ROOT, "roles", "gitops", "tasks")
VARS = os.path.join(REPO_ROOT, "roles", "gitops", "vars", "main.yml")
HELPER = os.path.join(TASKS_DIR, "_stage_tracked_paths.yml")
GITIGNORE = os.path.join(REPO_ROOT, "roles", "gitops", "files", "gitignore.default")

SITES = {
    "init_compose.yml": "gitops_compose_tracked_paths",
    "init_kubernetes.yml": "gitops_k8s_tracked_paths",
    "push_kubernetes.yml": "gitops_k8s_tracked_paths",
}

# Files that hold secrets or host-local state and must never be staged by name.
NEVER_STAGED = {
    ".env", "my.cnf", ".gitops-host", ".gitops-deploy-key", ".gitops-applied",
    ".gitops-pull-baseline",
    "config/wikis.yaml", "config/secrets.env", "images",
}


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


def _cmd_text(t):
    for key in ("ansible.builtin.command", "command",
                "ansible.builtin.shell", "shell"):
        c = t.get(key)
        if c is None:
            continue
        if isinstance(c, dict):
            if c.get("argv"):
                return " ".join(str(a) for a in c["argv"])
            return c.get("cmd", "")
        return str(c)
    return ""


def _include(t):
    return t.get("ansible.builtin.include_tasks") or t.get("include_tasks") or ""


def _helper_cmd():
    return _cmd_text(_load(HELPER)[0])


def _render_helper(paths):
    """Render the helper's shell with the same quoting Ansible's quote filter
    applies (shlex.quote)."""
    joined = " ".join(shlex.quote(p) for p in paths)
    return _helper_cmd().replace(
        "{{ _stage_paths | map('quote') | join(' ') }}", joined)


class TestNoWholeTreeStaging:
    def test_no_git_add_all_without_pathspec(self):
        offenders = []
        for name in sorted(os.listdir(TASKS_DIR)):
            if not name.endswith(".yml"):
                continue
            for t in _walk(_load(os.path.join(TASKS_DIR, name))):
                for line in _cmd_text(t).splitlines():
                    words = line.split()
                    for i in range(len(words) - 1):
                        if words[i] != "git" or words[i + 1] != "add":
                            continue
                        rest = words[i + 2:]
                        sweeping = rest[:1] in (["-A"], ["--all"], ["."])
                        if sweeping and "--" not in rest:
                            offenders.append("%s: %s" % (name, line.strip()))
        assert not offenders, (
            "gitops tasks must not stage the whole tree; use "
            "_stage_tracked_paths.yml or name the paths: %r" % offenders)

    @pytest.mark.parametrize("site,list_var", sorted(SITES.items()))
    def test_site_uses_explicit_path_list(self, site, list_var):
        stages = [t for t in _walk(_load(os.path.join(TASKS_DIR, site)))
                  if _include(t) == "_stage_tracked_paths.yml"]
        assert len(stages) == 1, (
            "%s must stage through _stage_tracked_paths.yml" % site)
        assert list_var in str(stages[0].get("vars", {}).get("_stage_paths")), (
            "%s must stage %s" % (site, list_var))

    def test_helper_stages_with_pathspec(self):
        assert 'git add -A -- "$@"' in _helper_cmd()


class TestPathLists:
    def test_lists_cover_repo_layout(self):
        v = _load(VARS)
        common = {".gitignore", ".gitmodules", "hosts", "config",
                  "extensions", "skins", "public_assets"}
        assert common | {".gitattributes", "env.template",
                         "wikis.yaml.template", "my.cnf.template",
                         "media-hosts.yaml.template",
                         "docker-compose.override.yml"} \
            <= set(v["gitops_compose_tracked_paths"])
        assert common | {".sops.yaml", "Chart.yaml", "templates",
                         "values.yaml", "values.template.yaml", "argocd"} \
            <= set(v["gitops_k8s_tracked_paths"])

    def test_lists_exclude_secret_and_local_files(self):
        v = _load(VARS)
        for key in ("gitops_compose_tracked_paths", "gitops_k8s_tracked_paths"):
            assert not NEVER_STAGED & set(v[key]), key


class TestComposeInitGuard:
    def test_gitcrypt_check_between_staging_and_commit(self):
        names = []
        for t in _walk(_load(os.path.join(TASKS_DIR, "init_compose.yml"))):
            if _include(t) in ("_stage_tracked_paths.yml",
                               "_check_gitcrypt_staged.yml"):
                names.append(_include(t))
            elif "commit -m 'Initial gitops configuration'" in _cmd_text(t):
                names.append("commit")
        assert names == ["_stage_tracked_paths.yml",
                         "_check_gitcrypt_staged.yml", "commit"], names


class TestGitignoreBackups:
    def test_editor_backup_patterns_ignored(self):
        with open(GITIGNORE) as f:
            lines = {ln.strip() for ln in f}
        for pat in ("*.bak", "*~", "*.swp", "*.swo", ".#*"):
            assert pat in lines, pat


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
class TestHelperBehavior:
    def _git(self, repo, *args):
        return subprocess.run(["git", *args], cwd=repo, check=True,
                              capture_output=True, text=True).stdout

    def _write(self, repo, rel, text="x\n"):
        path = os.path.join(repo, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)

    def test_stages_only_listed_paths_including_deletions(self, tmp_path):
        repo = str(tmp_path)
        self._git(repo, "init", "-q", "-b", "main")
        shutil.copy(GITIGNORE, os.path.join(repo, ".gitignore"))
        self._write(repo, "config/settings/global/Old.php")
        self._write(repo, "Dockerfile.custom")
        self._git(repo, "add", "-A")
        self._git(repo, "-c", "user.name=t", "-c", "user.email=t@example.com",
                  "-c", "commit.gpgsign=false", "commit", "-q", "-m", "base")

        os.remove(os.path.join(repo, "config/settings/global/Old.php"))
        self._write(repo, "config/settings/global/New.php")
        self._write(repo, "config/settings/global/New.php~")
        self._write(repo, "config/settings/global/.New.php.swp")
        self._write(repo, "hosts/h1/vars.yaml")
        self._write(repo, ".env")
        self._write(repo, ".env~")
        self._write(repo, "stray.sql")
        self._write(repo, "Dockerfile.custom", "changed\n")

        paths = _load(VARS)["gitops_compose_tracked_paths"]
        subprocess.run(["sh", "-c", _render_helper(paths)], cwd=repo,
                       check=True, capture_output=True, text=True)

        staged = set(self._git(repo, "diff", "--cached", "--no-renames",
                               "--name-status")
                     .splitlines())
        assert staged == {
            "D\tconfig/settings/global/Old.php",
            "A\tconfig/settings/global/New.php",
            "A\thosts/h1/vars.yaml",
            "M\tDockerfile.custom",
        }, staged

    def test_no_matching_paths_is_a_no_op(self, tmp_path):
        repo = str(tmp_path)
        self._git(repo, "init", "-q", "-b", "main")
        self._write(repo, "stray.txt")
        subprocess.run(["sh", "-c", _render_helper(["config", "values.yaml"])],
                       cwd=repo, check=True, capture_output=True, text=True)
        assert self._git(repo, "diff", "--cached", "--name-only") == ""
