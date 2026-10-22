"""Maintenance scripts run as the web server's user on Compose.

Files a maintenance script creates (thumbnails, file cache, localisation
cache, imported uploads) must be changeable by the web server afterwards,
so the scripts run as www-data rather than the container's default root.
Kubernetes is unchanged: kubectl exec cannot switch users.
"""

import os
import shlex
import subprocess
import sys

import jinja2
import pytest
import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, REPO_ROOT)

import direct_commands  # noqa: E402
from direct_commands import _helpers, maintenance  # noqa: E402

EXEC_YML = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks",
                        "exec.yml")
RUN_UPDATE_PHP_YML = os.path.join(REPO_ROOT, "roles", "mediawiki", "tasks",
                                  "run_update_php.yml")


class _FakePopen:
    instances = []

    def __init__(self, argv, **kw):
        self.argv = argv
        self.stdout = _NoLines()
        _FakePopen.instances.append(self)

    def wait(self, timeout=None):
        return 0


class _NoLines:
    def readline(self):
        return ""


@pytest.fixture
def popen(monkeypatch):
    _FakePopen.instances = []
    monkeypatch.setattr(_helpers.subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(_helpers, "_resolve_ssh_target", lambda h: h)
    monkeypatch.setattr(_helpers, "_ssh_args", lambda: [])
    return _FakePopen.instances


class TestStreamInContainerUser:
    def test_local_compose_runs_as_user(self, popen):
        inst = {"host": "localhost", "path": "/srv/mysite",
                "composeCommand": "docker compose"}
        _helpers._stream_in_container("mysite", inst, "php -v",
                                      user="www-data")
        assert popen[0].argv == [
            "docker", "compose", "exec", "-T", "--user", "www-data", "web",
            "/bin/bash", "-c", "stdbuf -oL php -v",
        ]

    def test_local_podman_compose_runs_as_user(self, popen):
        inst = {"host": "localhost", "path": "/srv/mysite",
                "composeCommand": "podman-compose"}
        _helpers._stream_in_container("mysite", inst, "php -v",
                                      user="www-data")
        assert popen[0].argv == [
            "podman-compose", "exec", "-T", "--user", "www-data", "web",
            "/bin/bash", "-c", "stdbuf -oL php -v",
        ]

    def test_remote_compose_runs_as_user(self, popen):
        inst = {"host": "remote1", "path": "/srv/mysite",
                "composeCommand": "docker compose"}
        _helpers._stream_in_container("mysite", inst, "php -v",
                                      user="www-data")
        assert popen[0].argv == [
            "ssh", "remote1",
            "cd '/srv/mysite' && docker compose exec -T --user www-data "
            "web /bin/bash -c 'stdbuf -oL php -v'",
        ]

    def test_compose_without_user_keeps_default(self, popen):
        inst = {"host": "localhost", "path": "/srv/mysite",
                "composeCommand": "docker compose"}
        _helpers._stream_in_container("mysite", inst, "php -v")
        assert "--user" not in popen[0].argv

    def test_local_k8s_ignores_user(self, popen, monkeypatch):
        monkeypatch.setattr(_helpers, "_k8s_get_pod", lambda ns, svc: "web-1")
        inst = {"host": "localhost", "orchestrator": "kubernetes"}
        _helpers._stream_in_container("mysite", inst, "php -v",
                                      user="www-data")
        assert popen[0].argv == [
            "kubectl", "exec", "-i", "web-1", "-n", "canasta-mysite",
            "-c", "web", "--", "/bin/bash", "-c", "stdbuf -oL php -v",
        ]

    def test_remote_k8s_ignores_user(self, popen):
        inst = {"host": "remote1", "orchestrator": "kubernetes"}
        _helpers._stream_in_container("mysite", inst, "php -v",
                                      user="www-data")
        assert popen[0].argv == [
            "ssh", "remote1",
            _helpers._k8s_remote_exec_cmd("mysite", "web",
                                          "stdbuf -oL php -v"),
        ]


class TestExecInContainerUser:
    def test_local_compose_runs_as_user(self, monkeypatch):
        captured = {}

        def fake_run(argv, **kw):
            captured["argv"] = argv
            return subprocess.CompletedProcess(argv, 0, "", "")
        monkeypatch.setattr(_helpers.subprocess, "run", fake_run)
        inst = {"host": "localhost", "path": "/srv/mysite",
                "composeCommand": "docker compose"}
        _helpers._exec_in_container("mysite", inst, "true", user="www-data")
        assert captured["argv"] == [
            "docker", "compose", "exec", "-T", "--user", "www-data", "web",
            "/bin/bash", "-c", "true",
        ]

    def test_remote_compose_runs_as_user(self, monkeypatch):
        captured = {}

        def fake_ssh(host, cmd, **kw):
            captured["cmd"] = cmd
            return 0, ""
        monkeypatch.setattr(_helpers, "_ssh_run", fake_ssh)
        inst = {"host": "remote1", "path": "/srv/mysite",
                "composeCommand": "docker compose"}
        _helpers._exec_in_container("mysite", inst, "true", user="www-data")
        assert captured["cmd"] == (
            "cd '/srv/mysite' && docker compose exec -T --user www-data "
            "web /bin/bash -c 'true'"
        )

    def test_local_k8s_ignores_user(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(_helpers, "_k8s_get_pod", lambda ns, svc: "web-1")

        def fake_run(argv, **kw):
            captured["argv"] = argv
            return subprocess.CompletedProcess(argv, 0, "", "")
        monkeypatch.setattr(_helpers.subprocess, "run", fake_run)
        inst = {"host": "localhost", "orchestrator": "kubernetes"}
        _helpers._exec_in_container("mysite", inst, "true", user="www-data")
        assert captured["argv"] == [
            "kubectl", "exec", "web-1", "-n", "canasta-mysite", "-c", "web",
            "--", "/bin/bash", "-c", "true",
        ]


def _args(**kw):
    defaults = {"id": "mysite", "wiki": None, "script_args": None,
                "skip_jobs": False, "skip_smw": False}
    defaults.update(kw)
    return type("Args", (), defaults)()


@pytest.fixture
def recorded(monkeypatch):
    """Record (command, user) for every stream and exec call."""
    calls = []
    inst = {"path": "/srv/mysite", "host": "localhost"}
    monkeypatch.setattr(_helpers, "_resolve_instance",
                        lambda args: ("mysite", inst))
    monkeypatch.setattr(_helpers, "_refuse_unfinished_pull",
                        lambda iid, i: False)
    monkeypatch.setattr(maintenance, "_read_wiki_ids", lambda i: ["main"])

    def stream(iid, i, command, service="web", retry_on_reset=False,
               user=None):
        calls.append((command, user))
        return 0

    def exec_(iid, i, command, service="web", user=None):
        calls.append((command, user))
        if command.startswith("test -f"):
            return 0, "yes\n"
        return 0, "SMW_LOADED=1\n"
    monkeypatch.setattr(_helpers, "_stream_in_container", stream)
    monkeypatch.setattr(_helpers, "_exec_in_container", exec_)
    return calls


class TestMaintenanceCommandsPassWebUser:
    def test_script_runs_as_web_user(self, recorded):
        direct_commands.cmd_maintenance_script(
            _args(script_args=["refreshLinks.php"]))
        assert recorded == [
            ("php maintenance/run.php refreshLinks.php --wiki='main'",
             "www-data"),
        ]

    def test_extension_script_runs_as_web_user(self, recorded):
        direct_commands.cmd_maintenance_extension(
            _args(script_args=["SemanticMediaWiki:rebuildData.php"]))
        assert recorded == [
            ("php maintenance/run.php SemanticMediaWiki:rebuildData.php "
             "--wiki='main'", "www-data"),
        ]

    def test_update_runs_every_php_step_as_web_user(self, recorded):
        direct_commands.cmd_maintenance_update(_args())
        users = {}
        for command, user in recorded:
            for step in ("update.php", "runJobs.php", "run.php eval",
                         "rebuildData.php"):
                if step in command:
                    users[step] = user
        assert users == {
            "update.php": "www-data",
            "runJobs.php": "www-data",
            "run.php eval": "www-data",
            "rebuildData.php": "www-data",
        }

    def test_listings_keep_the_default_user(self, recorded):
        direct_commands.cmd_maintenance_script(_args())
        direct_commands.cmd_maintenance_extension(_args())
        assert [u for _, u in recorded] == [None, None]


def _render_compose_exec(extra):
    with open(EXEC_YML) as f:
        tasks = yaml.safe_load(f)
    build = next(t for t in tasks
                 if t.get("name") == "Build exec command (Compose)")
    template = build["ansible.builtin.set_fact"]["_exec_full_cmd"]
    env = jinja2.Environment()
    env.filters["quote"] = shlex.quote
    variables = {"compose_command": "docker compose",
                 "_exec_shell": "sh", "exec_command": "php x.php"}
    variables.update(extra)
    return " ".join(env.from_string(template).render(**variables).split())


class TestExecYmlUser:
    def test_compose_exec_runs_as_exec_user(self):
        assert _render_compose_exec({"exec_user": "www-data"}) == (
            "docker compose exec -T --user www-data web sh 'php x.php'")

    def test_compose_exec_defaults_to_image_user(self):
        assert _render_compose_exec({}) == (
            "docker compose exec -T web sh 'php x.php'")

    def test_k8s_exec_has_no_user(self):
        with open(EXEC_YML) as f:
            text = f.read()
        k8s = text.split("Build K8s exec command", 1)[1].split("- name:", 1)[0]
        assert "exec_user" not in k8s

    def test_run_update_php_runs_as_web_user(self):
        with open(RUN_UPDATE_PHP_YML) as f:
            tasks = yaml.safe_load(f)
        run = next(t for t in tasks
                   if t.get("name") == "Run update.php for each wiki")
        assert run["vars"]["exec_user"] == "www-data"
