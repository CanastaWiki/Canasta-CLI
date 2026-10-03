"""gitops init and join read admin passwords on the instance's host.

A file lookup runs on the controller, so for a remote instance it never
sees admin-password_<id>. Both flows now slurp the files on the target
through _read_admin_passwords.yml.
"""

import json
import os
import subprocess
import sys

import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TASKS = os.path.join(REPO_ROOT, "roles", "gitops", "tasks")
INIT = os.path.join(TASKS, "init_compose.yml")
JOIN = os.path.join(TASKS, "join.yml")
READ = os.path.join(TASKS, "_read_admin_passwords.yml")


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _task(tasks, name):
    for t in tasks:
        if t.get("name") == name:
            return t
    raise AssertionError(f"task {name!r} not found")


def _include_of(tasks):
    return [t for t in tasks
            if "_read_admin_passwords.yml"
            in str(t.get("ansible.builtin.include_tasks", ""))]


class TestNoControllerLookup:
    def test_no_admin_password_lookup(self):
        for path in (INIT, JOIN):
            with open(path) as f:
                text = f.read()
            assert "lookup('file', pw_file" not in text
            assert "'/admin-password_'" not in text, path

    def test_join_has_no_ignore_errors_on_password_vars(self):
        task = _task(_load(JOIN), "Add admin password vars")
        assert "ignore_errors" not in task
        assert task.get("no_log") is True

    def test_both_flows_include_reader_unconditionally(self):
        for path in (INIT, JOIN):
            includes = _include_of(_load(path))
            assert len(includes) == 1, path
            assert "when" not in includes[0]

    def test_reader_slurps_in_a_loop_without_lookup(self):
        tasks = _load(READ)
        slurp = _task(tasks, "Read admin password files")
        assert "ansible.builtin.slurp" in slurp
        assert "loop" in slurp
        assert "when" not in slurp
        assert "ignore_errors" not in slurp
        assert slurp.get("no_log") is True
        assert "file not found" in slurp["failed_when"]
        assert _task(tasks, "Collect admin passwords").get("no_log") is True
        with open(READ) as f:
            assert "lookup(" not in f.read()


def _run_reader(tmp_path, wiki_ids):
    out = tmp_path / "out.json"
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {"instance_path": str(tmp_path / "inst"),
                 "admin_password_wiki_ids": wiki_ids},
        "tasks": [
            {"ansible.builtin.include_tasks": READ},
            {"ansible.builtin.copy": {
                "content": "{{ _gitops_admin_passwords | to_json }}",
                "dest": str(out)}},
        ],
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0")
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run(
        [playbook, "-i", "localhost,", str(play)],
        capture_output=True, text=True, env=env, cwd=tmp_path)
    return proc, (json.loads(out.read_text()) if out.exists() else None)


class TestReaderBehavior:
    def test_reads_present_and_omits_missing(self, tmp_path):
        inst = tmp_path / "inst"
        inst.mkdir()
        (inst / "admin-password_main").write_text('p"w\\1 #x')
        (inst / "admin-password_docs").write_text("second")
        proc, data = _run_reader(tmp_path, ["main", "gone", "docs"])
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert data == {"admin_password_main": 'p"w\\1 #x',
                        "admin_password_docs": "second"}
        assert 'p"w' not in proc.stdout

    def test_no_wikis_yields_empty_dict(self, tmp_path):
        (tmp_path / "inst").mkdir()
        proc, data = _run_reader(tmp_path, [])
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert data == {}

    def test_unreadable_entry_fails(self, tmp_path):
        inst = tmp_path / "inst"
        (inst / "admin-password_main").mkdir(parents=True)
        proc, data = _run_reader(tmp_path, ["main"])
        assert proc.returncode != 0
        assert data is None
