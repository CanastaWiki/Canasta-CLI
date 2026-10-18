"""A failed startup composer update is reported after start, restart and
upgrade, and by doctor.

The web container still comes up healthy on the image's bundled vendor/ when
composer fails, so nothing else tells the operator that the packages in
config/composer.local.json were not installed. The image records the failure
in config/persistent/composer-update.failed; older images never write it.
"""

import os
import subprocess
import sys

import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, REPO_ROOT)

from direct_commands import _helpers, doctor  # noqa: E402

REPORT = os.path.join(REPO_ROOT, "roles", "mediawiki", "tasks",
                      "report_composer_failure.yml")
FIRST_LINE = "Composer update failed at 2026-10-14T12:00:00Z (exit 2)."
RECORD = FIRST_LINE + "\nconfig/composer.local.json could not be parsed.\n"
INST = {"id": "test", "path": "/srv/test", "host": "localhost",
        "orchestrator": "compose"}


def _container(tmp_path, record=None):
    """An _exec_in_container stand-in that runs the command under bash with
    the container's /mediawiki redirected into tmp_path."""
    root = tmp_path / "mediawiki"
    (root / "config" / "persistent").mkdir(parents=True)
    if record is not None:
        (root / "config" / "persistent" / "composer-update.failed") \
            .write_text(record)

    def run(inst_id, inst, command, service="web"):
        proc = subprocess.run(
            ["bash", "-c", command.replace("/mediawiki/", str(root) + "/")],
            capture_output=True, text=True)
        return proc.returncode, proc.stdout
    return run


class TestSummary:
    def test_reads_the_first_line(self, monkeypatch, tmp_path):
        monkeypatch.setattr(_helpers, "_exec_in_container",
                            _container(tmp_path, RECORD))
        assert _helpers._composer_failure_summary("test", INST) == FIRST_LINE

    def test_no_record_is_nothing_to_report(self, monkeypatch, tmp_path):
        monkeypatch.setattr(_helpers, "_exec_in_container",
                            _container(tmp_path))
        assert _helpers._composer_failure_summary("test", INST) is None

    def test_an_unreadable_container_is_nothing_to_report(self, monkeypatch):
        monkeypatch.setattr(_helpers, "_exec_in_container",
                            lambda *a, **kw: (1, FIRST_LINE))
        assert _helpers._composer_failure_summary("test", INST) is None


def _start(monkeypatch, summary, ready=0):
    monkeypatch.setattr(subprocess, "call", lambda cmd, **kw: 0)
    monkeypatch.setattr(_helpers, "_resolve_instance",
                        lambda args: ("test", dict(INST)))
    monkeypatch.setattr(_helpers, "_compose_file_args",
                        lambda *a, **kw: ["-f", "docker-compose.yml"])
    monkeypatch.setattr(_helpers, "_sync_compose_profiles", lambda inst: {})
    monkeypatch.setattr(_helpers, "_backfill_db_defaults", lambda inst: None)
    monkeypatch.setattr(_helpers, "_uses_wiki_db_account", lambda env: False)
    monkeypatch.setattr(_helpers, "_crowdsec_setup_pending",
                        lambda inst, env: False)
    monkeypatch.setattr(_helpers, "_missing_db_password",
                        lambda inst, env: False)
    monkeypatch.setattr(_helpers, "_instance_has_sidecars", lambda inst: False)
    monkeypatch.setattr(_helpers, "_check_running_compose",
                        lambda *a, **kw: False)
    monkeypatch.setattr(_helpers, "_run_compose", lambda *a, **kw: 0)
    monkeypatch.setattr(_helpers, "_wait_web_ready", lambda i, inst: ready)
    monkeypatch.setattr(_helpers, "_rootless_operator_chown",
                        lambda inst: None)
    reads = []

    def summarize(inst_id, inst):
        reads.append(inst_id)
        return summary
    monkeypatch.setattr(_helpers, "_composer_failure_summary", summarize)
    return reads


def _args():
    return type("Args", (), {"id": "test"})()


class TestDoctor:
    def test_a_failure_is_a_warning(self, monkeypatch):
        monkeypatch.setattr(_helpers, "_composer_failure_summary",
                            lambda i, inst: FIRST_LINE)
        lines = doctor._composer_failure_lines(INST)
        assert "Composer dependencies (test):" in lines
        assert any(line.startswith("  WARN — " + FIRST_LINE)
                   for line in lines)
        assert any("canasta maintenance exec -i test -- cat" in line
                   for line in lines)

    def test_no_record_adds_nothing(self, monkeypatch):
        monkeypatch.setattr(_helpers, "_composer_failure_summary",
                            lambda i, inst: None)
        assert doctor._composer_failure_lines(INST) == []


def _run_report(tmp_path, record=None, exec_rc=0):
    """Run report_composer_failure.yml against a stub exec.yml that runs the
    command locally with /mediawiki redirected into tmp_path."""
    root = tmp_path / "mediawiki"
    (root / "config" / "persistent").mkdir(parents=True)
    if record is not None:
        (root / "config" / "persistent" / "composer-update.failed") \
            .write_text(record)
    stub_root = tmp_path / "canasta"
    tasks = stub_root / "roles" / "orchestrator" / "tasks"
    tasks.mkdir(parents=True)
    (tasks / "exec.yml").write_text(yaml.safe_dump([{
        "name": "Stub exec",
        "ansible.builtin.shell": {
            "cmd": "{{ exec_command | replace('/mediawiki/', '%s/') }}"
                   "%s" % (root, "; exit 1" if exec_rc else ""),
            "executable": "/bin/bash",
        },
        "register": "exec_result",
        "changed_when": False,
    }]))
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {"canasta_root": str(stub_root), "instance_id": "test",
                 "ansible_python_interpreter": sys.executable},
        "tasks": [{"ansible.builtin.include_tasks": REPORT}],
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0")
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    return subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env,
                          cwd=tmp_path)


class TestAnsibleReport:
    def test_a_failure_is_reported(self, tmp_path):
        proc = _run_report(tmp_path, RECORD)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert ("Warning: %s Details: canasta maintenance exec -i test -- "
                "cat /mediawiki/config/persistent/composer-update.failed"
                % FIRST_LINE) in proc.stdout
        assert "could not be parsed" not in proc.stdout

    def test_no_record_reports_nothing(self, tmp_path):
        proc = _run_report(tmp_path)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "Warning:" not in proc.stdout

    def test_an_unreadable_container_does_not_fail_the_run(self, tmp_path):
        proc = _run_report(tmp_path, RECORD, exec_rc=1)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "Details: canasta maintenance exec" not in proc.stdout


def _includes_report(tasks):
    return any("report_composer_failure.yml"
               in str(t.get("ansible.builtin.include_tasks", ""))
               for t in tasks)


def _load(*parts):
    with open(os.path.join(REPO_ROOT, *parts)) as f:
        return yaml.safe_load(f)


class TestWiring:
    def test_start_and_restart_report_after_starting(self):
        for name in ("start.yml", "restart.yml"):
            tasks = _load("roles", "instance_lifecycle", "tasks", name)
            names = [t.get("name") for t in tasks]
            report = names.index("Report a failed startup composer update")
            assert report > names.index("Start containers"), name
            assert _includes_report(tasks[report:report + 1]), name

    def test_upgrade_reports_after_each_restart(self):
        tasks = _load("roles", "upgrade", "tasks", "main.yml")
        restart = next(t for t in tasks if t.get("name") == "Restart containers")
        names = [t.get("name") for t in restart["block"]]
        assert names.index("Report a failed startup composer update") \
            > names.index("Start containers")
        staged = next(t for t in tasks if t.get("name")
                      == "Leave the deploy of a gitops-managed instance to "
                         "Argo CD")
        rebuilt = next(t for t in staged["block"] if t.get("name")
                       == "Restart the web pods onto a rebuilt image")
        assert _includes_report(rebuilt["block"])
