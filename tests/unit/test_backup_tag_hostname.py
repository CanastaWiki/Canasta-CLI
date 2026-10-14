"""The host name in backup tags names the machine the instance runs on.

Under canasta-docker a local target's `hostname` runs inside the CLI
container, whose name is a random id, so the tag uses the host name
canasta-docker passes in. A native CLI, or any remote target, keeps using
the target's own `hostname`.
"""

import json
import os
import subprocess
import sys

import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
FILTER_DIR = os.path.join(REPO_ROOT, "filter_plugins")
BACKUP_TASKS = os.path.join(REPO_ROOT, "roles", "backup", "tasks")
TAG_HOSTNAME = os.path.join(BACKUP_TASKS, "_tag_hostname.yml")
PURGE = os.path.join(BACKUP_TASKS, "purge.yml")
CREATE = os.path.join(BACKUP_TASKS, "create.yml")

TARGET = "target-host"
DOCKER_HOST_NAME = "real-docker-host"

# A connection other than `local` that still runs commands on this machine,
# standing in for an SSH target.
STUB_CONNECTION = '''
from ansible.plugins.connection.local import Connection as _Local


class Connection(_Local):
    transport = "stubremote"
'''


def _env(tmp_path, run_mode, host_name):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    stub = bindir / "hostname"
    stub.write_text("#!/bin/sh\necho %s\n" % TARGET)
    stub.chmod(0o755)
    conn = tmp_path / "connection_plugins"
    conn.mkdir(exist_ok=True)
    (conn / "stubremote.py").write_text(STUB_CONNECTION)
    env = {k: v for k, v in os.environ.items()
           if k not in ("CANASTA_RUN_MODE", "CANASTA_HOST_NAME")}
    env.update(ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_FILTER_PLUGINS=FILTER_DIR,
               ANSIBLE_CONNECTION_PLUGINS=str(conn),
               PATH=str(bindir) + os.pathsep + env.get("PATH", ""))
    if run_mode is not None:
        env["CANASTA_RUN_MODE"] = run_mode
    if host_name is not None:
        env["CANASTA_HOST_NAME"] = host_name
    return env


def _run(tmp_path, tasks, play_vars, env):
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": dict({"ansible_python_interpreter": sys.executable},
                     **play_vars),
        "tasks": tasks,
    }]))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env,
                          cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc


def _tag_host(tmp_path, run_mode=None, host_name=None, connection="local"):
    out = tmp_path / "host.txt"
    tasks = [
        {"ansible.builtin.set_fact": {"ansible_connection": connection}},
        {"ansible.builtin.include_tasks": TAG_HOSTNAME},
        {"ansible.builtin.copy": {"content": "{{ _backup_tag_host }}",
                                  "dest": str(out)},
         "delegate_to": "localhost",
         "vars": {"ansible_connection": "local"}},
    ]
    _run(tmp_path, tasks, {}, _env(tmp_path, run_mode, host_name))
    return out.read_text()


class TestTagHost:
    def test_docker_mode_local_target_uses_the_passed_host_name(self, tmp_path):
        assert _tag_host(tmp_path, "docker", DOCKER_HOST_NAME) == \
            DOCKER_HOST_NAME

    def test_native_uses_hostname(self, tmp_path):
        assert _tag_host(tmp_path) == TARGET

    def test_native_ignores_a_stray_host_name(self, tmp_path):
        assert _tag_host(tmp_path, "native", DOCKER_HOST_NAME) == TARGET

    def test_docker_mode_remote_target_uses_its_hostname(self, tmp_path):
        assert _tag_host(tmp_path, "docker", DOCKER_HOST_NAME,
                         connection="stubremote") == TARGET

    def test_docker_mode_without_a_host_name_falls_back(self, tmp_path):
        assert _tag_host(tmp_path, "docker", "") == TARGET


def test_purge_maps_keep_tag_to_the_docker_host_name(tmp_path):
    root = tmp_path / "root"
    (root / "roles" / "common" / "tasks").mkdir(parents=True)
    (root / "roles" / "orchestrator" / "tasks").mkdir(parents=True)
    (root / "roles" / "common" / "tasks" / "resolve_instance.yml").write_text(
        "- ansible.builtin.meta: noop\n")
    out = tmp_path / "args.json"
    (root / "roles" / "orchestrator" / "tasks" / "run_backup.yml").write_text(
        yaml.safe_dump([
            {"ansible.builtin.copy": {
                "content": "{{ backup_args | to_json }}", "dest": str(out)}},
            {"ansible.builtin.set_fact": {"_restic_result": {"stdout": ""}}},
        ]))
    _run(tmp_path, [{"ansible.builtin.include_tasks": PURGE}],
         {"canasta_root": str(root), "keep_tag": "keepme"},
         _env(tmp_path, "docker", DOCKER_HOST_NAME))
    assert json.loads(out.read_text()) == [
        "forget", "--prune", "--keep-tag", "keepme__on__" + DOCKER_HOST_NAME]


def test_create_builds_the_tag_from_the_shared_host_name():
    def walk(tasks):
        for t in tasks:
            yield t
            yield from walk(t.get("block", []))
    with open(CREATE) as f:
        tasks = list(walk(yaml.safe_load(f)))
    includes = [t.get("ansible.builtin.include_tasks") for t in tasks]
    assert "_tag_hostname.yml" in includes
    build = next(t for t in tasks if t.get("name") == "Build tag with hostname")
    assert "_backup_tag_host" in build["ansible.builtin.set_fact"]["_backup_tag"]
