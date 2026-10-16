"""Local commits waiting to be pushed are not a pull still to be applied.

A command that commits on the host without pushing moves HEAD past the
last-applied commit. Only a commit from the remote among those since the
applied one means a pull stopped before applying it; otherwise `gitops add`
must capture config/wikis.yaml and the .env and wikis.yaml syncs must run.
"""

import os
import subprocess
import sys

import pytest
import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TASKS = os.path.join(REPO_ROOT, "roles", "gitops", "tasks")
PENDING = os.path.join(TASKS, "_gitops_apply_pending.yml")
ADD = os.path.join(TASKS, "add.yml")
SYNC_WIKIS = os.path.join(TASKS, "sync_wikis_yaml.yml")
COMMON = os.path.join(REPO_ROOT, "roles", "common")

MAIN = "- id: main\n  url: {{wiki_url_main}}\n  name: Main\n"
DOCS = "- id: docs\n  url: {{wiki_url_docs}}\n  name: Docs\n"
LIVE = "wikis:\n- id: main\n  url: localhost/main\n  name: Main\n"
REFUSE = "Refuse to capture config/wikis.yaml before a pulled commit is applied"


def _git(cwd, *args):
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com",
         "-c", "commit.gpgsign=false", *args],
        cwd=cwd, check=True, capture_output=True, text=True,
    ).stdout.strip()


def _commit_template(repo, content, message):
    (repo / "wikis.yaml.template").write_text(content)
    _git(repo, "add", "wikis.yaml.template")
    _git(repo, "commit", "-qm", message)


@pytest.fixture
def hosts(tmp_path):
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    a = tmp_path / "a"
    b = tmp_path / "b"
    _git(tmp_path, "clone", "-q", str(origin), str(a))
    _git(a, "checkout", "-q", "-b", "main")
    _commit_template(a, "wikis:\n" + MAIN, "base")
    _git(a, "push", "-q", "-u", "origin", "main")
    _git(tmp_path, "clone", "-q", str(origin), str(b))
    base = _git(a, "rev-parse", "HEAD")
    for repo in (a, b):
        (repo / "config").mkdir()
        (repo / "config" / "wikis.yaml").write_text(LIVE)
        # Written without a trailing newline, as pull does.
        (repo / ".gitops-applied").write_text(base)
    return a, b


def _local_commit(a):
    _commit_template(a, "wikis:\n" + MAIN + DOCS, "local")


def _stopped_pull(a, b, rebase=False):
    _commit_template(b, "wikis:\n" + MAIN + DOCS, "remote")
    _git(b, "push", "-q")
    _git(a, "pull", "-q", *(["--rebase"] if rebase else []))


def _run(tmp_path, tasks, inst):
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost", "connection": "local", "gather_facts": False,
        "vars": {"instance_path": str(inst), "canasta_root": REPO_ROOT,
                 "ansible_python_interpreter": sys.executable},
        "tasks": tasks,
    }]))
    base = {k: v for k, v in os.environ.items() if not k.startswith("ANSIBLE_")}
    env = dict(base, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_COLLECTIONS_PATH=str(tmp_path / "no-collections"),
               ANSIBLE_FILTER_PLUGINS=os.path.join(REPO_ROOT, "filter_plugins"),
               ANSIBLE_LIBRARY=os.path.join(COMMON, "library"),
               ANSIBLE_MODULE_UTILS=os.path.join(COMMON, "module_utils"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    return subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env,
                          cwd=tmp_path)


def _pending(tmp_path, inst):
    out = tmp_path / "pending.txt"
    proc = _run(tmp_path, [
        {"ansible.builtin.include_tasks": PENDING},
        {"ansible.builtin.copy": {"content": "{{ _gitops_apply_pending }}",
                                  "dest": str(out)}},
    ], inst)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return out.read_text().strip()


class TestApplyPending:
    def test_applied_checkout_is_not_pending(self, tmp_path, hosts):
        a, _ = hosts
        assert _pending(tmp_path, a) == "False"

    def test_local_commit_is_not_pending(self, tmp_path, hosts):
        a, _ = hosts
        _local_commit(a)
        assert _pending(tmp_path, a) == "False"

    def test_local_commit_with_remote_commits_waiting_is_not_pending(
            self, tmp_path, hosts):
        a, b = hosts
        _commit_template(b, "wikis:\n" + MAIN + "# remote\n", "remote")
        _git(b, "push", "-q")
        _local_commit(a)
        _git(a, "fetch", "-q")
        assert _pending(tmp_path, a) == "False"

    def test_stopped_pull_is_pending(self, tmp_path, hosts):
        a, b = hosts
        _stopped_pull(a, b)
        assert _pending(tmp_path, a) == "True"

    def test_stopped_rebased_pull_is_pending(self, tmp_path, hosts):
        a, b = hosts
        _local_commit(a)
        _commit_template(b, "wikis:\n" + MAIN + "# remote\n", "remote")
        _git(b, "push", "-q")
        _git(a, "pull", "-q", "--rebase", "-X", "theirs")
        assert _pending(tmp_path, a) == "True"


def _add_refusal_tasks():
    with open(ADD) as f:
        tasks = {t.get("name"): t for t in yaml.safe_load(f)}
    check = dict(tasks["Check for a pull still to be applied"])
    check["ansible.builtin.include_tasks"] = PENDING
    check["when"] = True
    return [{"ansible.builtin.set_fact": {"_add_has_wikis": True}},
            check, tasks[REFUSE]]


class TestAddCapture:
    def test_add_captures_after_a_local_commit(self, tmp_path, hosts):
        a, _ = hosts
        _local_commit(a)
        proc = _run(tmp_path, _add_refusal_tasks(), a)
        assert proc.returncode == 0, proc.stdout + proc.stderr

    def test_add_refuses_after_a_stopped_pull(self, tmp_path, hosts):
        a, b = hosts
        _stopped_pull(a, b)
        proc = _run(tmp_path, _add_refusal_tasks(), a)
        assert proc.returncode != 0
        assert "stopped before" in proc.stdout + proc.stderr


class TestWikisSync:
    def test_live_edit_is_captured_after_a_local_commit(self, tmp_path, hosts):
        a, _ = hosts
        _local_commit(a)
        proc = _run(tmp_path, [{"ansible.builtin.include_tasks": SYNC_WIKIS}], a)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "id: docs" not in (a / "wikis.yaml.template").read_text()

    def test_stopped_pull_is_not_captured_back(self, tmp_path, hosts):
        a, b = hosts
        _stopped_pull(a, b)
        proc = _run(tmp_path, [{"ansible.builtin.include_tasks": SYNC_WIKIS}], a)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "id: docs" in (a / "wikis.yaml.template").read_text()
