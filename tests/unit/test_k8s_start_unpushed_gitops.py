"""A Kubernetes start on a gitops instance names the changes it deploys
that are not pushed, since Argo CD puts the pushed state back."""

import os
import subprocess
import sys

import jinja2
import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MODULE_UTILS = os.path.join(REPO_ROOT, "roles", "common", "module_utils")
sys.path.insert(0, MODULE_UTILS)

import canasta_gitops_pending as pending  # noqa: E402

TASKS = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks")
WARN = os.path.join(TASKS, "k8s_warn_unpushed_gitops.yml")
START = os.path.join(TASKS, "start.yml")

ENV = "MW_SITE_FQDN=wiki.example.com\nMYSQL_PASSWORD=s3cret\nMW_ENABLE_JOB_RUNNER=true\n"
WIKIS = "wikis:\n- id: main\n  url: wiki.example.com\n  name: Main\n"
GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@t",
       "-c", "commit.gpgsign=false"]


def _secret_regex():
    with open(os.path.join(REPO_ROOT, "vars", "secret_classification.yml")) as f:
        data = yaml.safe_load(f)
    return jinja2.Template(data["canasta_secret_key_regex"]).render(**data)


def _write(root, rel, content):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _git(root, *args):
    subprocess.run(GIT + ["-C", str(root)] + list(args), check=True,
                   capture_output=True)


def _render(root):
    env = "\n".join(ln for ln in (root / ".env").read_text().splitlines()
                    if not ln.startswith("MYSQL_PASSWORD")) + "\n"
    return yaml.safe_dump({
        "configData": {"web": {
            ".env": env, "wikis.yaml": (root / "config/wikis.yaml").read_text(),
            "settings--global--Main.php": "<?php\n",
        }},
        "appSecretEnv": [],
    })


def _instance(tmp_path, gitops=True):
    """A pushed, clean single-env K8s gitops checkout with an upstream."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    root = tmp_path / "inst"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _write(root, ".gitignore", ".env\nconfig/wikis.yaml\n")
    if gitops:
        _write(root, ".gitops-host", "h1\n")
    _write(root, ".env", ENV)
    _write(root, "config/wikis.yaml", WIKIS)
    _write(root, "values.yaml", _render(root))
    _write(root, "config/settings/global/Main.php", "<?php\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "init")
    _git(root, "remote", "add", "origin", str(remote))
    _git(root, "push", "-q", "-u", "origin", "main")
    return root


def _probe(root):
    env = dict(os.environ, GIT_CEILING_DIRECTORIES=str(root.parent))
    out = subprocess.run(
        ["bash", "-c", pending.unpushed_probe_script(str(root))],
        capture_output=True, text=True, timeout=30, env=env).stdout
    return pending.unpushed_changes(out, _secret_regex())


class TestUnpushedChanges:
    def test_clean_instance_has_nothing_unpushed(self, tmp_path):
        assert _probe(_instance(tmp_path)) == []

    def test_not_gitops_managed_reports_nothing(self, tmp_path):
        root = _instance(tmp_path, gitops=False)
        _write(root, "config/settings/global/Main.php", "<?php // edit\n")
        assert _probe(root) == []

    def test_unstaged_change(self, tmp_path):
        root = _instance(tmp_path)
        _write(root, "config/settings/global/Main.php", "<?php // edit\n")
        assert _probe(root) == ["config/settings/global/Main.php"]

    def test_staged_change(self, tmp_path):
        root = _instance(tmp_path)
        _write(root, "values.yaml", _render(root) + "# staged\n")
        _git(root, "add", "values.yaml")
        assert _probe(root) == ["values.yaml"]

    def test_render_input_change(self, tmp_path):
        root = _instance(tmp_path)
        _write(root, ".env", ENV.replace("JOB_RUNNER=true", "JOB_RUNNER=false"))
        assert _probe(root) == [".env"]

    def test_secret_only_env_change_is_not_reported(self, tmp_path):
        root = _instance(tmp_path)
        _write(root, ".env", ENV.replace("s3cret", "other"))
        assert _probe(root) == []

    def test_local_commit_not_pushed(self, tmp_path):
        root = _instance(tmp_path)
        _write(root, "values.yaml", _render(root) + "# committed\n")
        _git(root, "commit", "-qam", "local")
        assert _probe(root) == ["1 local commit"]

    def test_probe_leaves_the_checkout_unchanged(self, tmp_path):
        root = _instance(tmp_path)
        _write(root, "config/settings/global/Main.php", "<?php // edit\n")
        index = root / ".git" / "index"
        before = (index.read_bytes(), index.stat().st_mtime_ns)
        refs = subprocess.run(["git", "-C", str(root), "show-ref"],
                              capture_output=True, text=True).stdout
        _probe(root)
        assert (index.read_bytes(), index.stat().st_mtime_ns) == before
        assert subprocess.run(["git", "-C", str(root), "show-ref"],
                              capture_output=True, text=True).stdout == refs


def _run_warn(tmp_path, root):
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {"instance_path": str(root), "instance_id": "mysite",
                 "canasta_secret_key_regex": _secret_regex()},
        "tasks": [{"ansible.builtin.include_tasks": WARN}],
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_COLLECTIONS_PATH=str(tmp_path / "ac"),
               ANSIBLE_FILTER_PLUGINS=os.path.join(REPO_ROOT, "filter_plugins"),
               GIT_CEILING_DIRECTORIES=str(root.parent))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    return subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env, cwd=tmp_path)


class TestStartWarning:
    def test_warns_with_push_and_sync(self, tmp_path):
        root = _instance(tmp_path)
        _write(root, "config/wikis.yaml", WIKIS + "  extra_databases:\n  - x\n")
        proc = _run_warn(tmp_path, root)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "'mysite' has changes not yet pushed: config/wikis.yaml." \
            in proc.stdout
        assert "canasta gitops push -i mysite" in proc.stdout
        assert "canasta gitops sync -i mysite" in proc.stdout
        assert "s3cret" not in proc.stdout

    def test_silent_when_clean(self, tmp_path):
        proc = _run_warn(tmp_path, _instance(tmp_path))
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "has changes not yet pushed" not in proc.stdout


def test_k8s_start_warns_before_the_helm_deploy():
    with open(START) as f:
        tasks = yaml.safe_load(f)
    k8s = next(t for t in tasks if t.get("name") == "Start (Kubernetes)")
    includes = [str(t.get("ansible.builtin.include_tasks", ""))
                for t in k8s["block"]]
    warn = includes.index("k8s_warn_unpushed_gitops.yml")
    assert warn < includes.index("k8s_start_helm_deploy.yml")
    assert "when" not in k8s["block"][warn]
