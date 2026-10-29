"""Commands other than `start` recognize a paused Compose instance.

A paused container is listed by `ps` without -a, so it read as running:
`list` and `status` said RUNNING, `doctor` ran checks inside it that the
runtime refused, and commands that exec into it failed with the runtime's
error. `list`, `status` and `doctor` now say it is paused, and commands
that work inside its containers stop with a plain message instead of
resuming it, since pausing is deliberate.
"""

import os
import stat
import subprocess
import sys

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
sys.path.insert(0, REPO_ROOT)

from direct_commands import _helpers, doctor  # noqa: E402

TASKS = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks")
REFUSE = os.path.join(TASKS, "refuse_paused.yml")
GUARDED = [os.path.join(REPO_ROOT, "roles", *p) for p in (
    ("config", "tasks", "set.yml"),
    ("config", "tasks", "unset.yml"),
    ("extensions_skins", "tasks", "enable.yml"),
    ("extensions_skins", "tasks", "disable.yml"),
    ("extensions_skins", "tasks", "add.yml"),
    ("extensions_skins", "tasks", "remove.yml"),
    ("extensions_skins", "tasks", "set_version.yml"),
)]


class TestWebState:
    def test_docker_paused_web_reads_as_paused(self):
        out = "abc|paused|Up 5 minutes (Paused)"
        assert _helpers._compose_web_state(out, lambda c: 0) == "PAUSED"

    def test_podman_paused_web_reads_as_paused(self):
        out = "abc|paused|Paused"
        assert _helpers._compose_web_state(out, lambda c: 0) == "PAUSED"

    def test_running_web_still_reads_as_running(self):
        out = "abc|running|Up 5 minutes (healthy)"
        assert _helpers._compose_web_state(out, lambda c: 0) == "RUNNING"


class TestPausedLookup:
    def _lookup(self, monkeypatch, rc, out, inspect="docker"):
        seen = []

        def capture(inst, argv, timeout=30):
            seen.append(argv)
            return rc, out
        monkeypatch.setattr(_helpers, "_runtime_capture", capture)
        inst = {"path": "/srv/MySite", "inspectCommand": inspect}
        return _helpers._compose_paused_services(inst), seen

    def test_names_each_paused_service_once(self, monkeypatch):
        names, seen = self._lookup(monkeypatch, 0, "web\ndb\nweb\n")
        assert names == ["db", "web"]
        argv = seen[0]
        assert "status=paused" in argv
        assert "label=com.docker.compose.project=mysite" in argv
        assert _helpers._PS_SERVICE_FORMAT["docker"] in argv

    def test_podman_uses_its_label_template(self, monkeypatch):
        _, seen = self._lookup(monkeypatch, 0, "", inspect="/usr/bin/podman")
        assert _helpers._PS_SERVICE_FORMAT["podman"] in seen[0]

    def test_unreadable_runtime_reads_as_none_paused(self, monkeypatch):
        names, _ = self._lookup(monkeypatch, 1, "web\n")
        assert names == []


class TestDoctor:
    def test_paused_lines_name_the_services_and_the_fix(self):
        lines = doctor._paused_lines({"id": "mysite"}, ["db", "web"])
        assert lines[1] == "Instance (mysite):"
        assert "paused (db, web)" in lines[2]
        assert "'canasta start -i mysite'" in lines[2]


FAKE_RUNTIME = """#!/bin/sh
case "$1" in
  ps) printf '%b' "$PAUSED" ;;
esac
"""


def _refuse(tmp_path, paused, k8s=False):
    inst = tmp_path / "MySite"
    inst.mkdir()
    fake = tmp_path / "docker"
    fake.write_text(FAKE_RUNTIME)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {
            "instance_path": str(inst),
            "instance_id": "mysite",
            "instance_is_k8s": k8s,
            "inspect_command": str(fake),
            "ansible_python_interpreter": sys.executable,
        },
        "tasks": [{"ansible.builtin.include_tasks": REFUSE}],
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               PAUSED=paused)
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    return subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env,
                          cwd=tmp_path, stdin=subprocess.DEVNULL)


class TestRefusePaused:
    def test_paused_instance_is_refused_with_a_plain_message(self, tmp_path):
        proc = _refuse(tmp_path, "c1 web\\nc2 db\\n")
        assert proc.returncode != 0
        out = " ".join((proc.stdout + proc.stderr).split())
        assert "Instance 'mysite' is paused (db, web)" in out
        assert "'canasta start -i mysite'" in out

    def test_running_instance_passes(self, tmp_path):
        proc = _refuse(tmp_path, "")
        assert proc.returncode == 0, proc.stdout + proc.stderr

    def test_kubernetes_is_not_checked(self, tmp_path):
        proc = _refuse(tmp_path, "c1 web\\n", k8s=True)
        assert proc.returncode == 0, proc.stdout + proc.stderr


def _tasks(path):
    with open(path) as f:
        return yaml.safe_load(f)


def test_each_command_refuses_right_after_resolving_the_instance():
    # After the pre-edge refusal where there is one.
    for path in GUARDED:
        tasks = _tasks(path)
        names = [t.get("name", "") for t in tasks]
        after = next(i for i, n in enumerate(names)
                     if n.startswith("Resolve instance"))
        if names[after + 1] == "Refuse an instance that predates the edge":
            after += 1
        guard = tasks[after + 1]
        assert guard.get("ansible.builtin.include_tasks", "").endswith(
            "/roles/orchestrator/tasks/refuse_paused.yml"), path


def test_config_set_refuses_only_when_it_resolves_the_instance():
    tasks = _tasks(GUARDED[0])
    guard = next(t for t in tasks if t.get("name") == "Refuse a paused instance")
    assert guard["when"] == "_config_set_top_level | bool"
    note = next(t for t in tasks
                if "_config_set_top_level" in (t.get("ansible.builtin.set_fact") or {}))
    assert note["ansible.builtin.set_fact"]["_config_set_top_level"] == (
        "{{ instance_path is not defined }}")


def test_caddy_reload_reports_a_paused_caddy():
    tasks = _tasks(os.path.join(TASKS, "reload_caddy.yml"))
    note = next(t for t in tasks
                if t.get("name") == "Report that a paused Caddy kept its previous config")
    assert "'caddy' in (_paused_services | default([]))" in note["when"]
    assert "canasta restart" in note["ansible.builtin.debug"]["msg"]
