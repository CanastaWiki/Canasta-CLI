"""Database administration reaches an external server on its own port."""

import json
import os
import subprocess
import sys

import pytest
import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DB_ADMIN = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks",
                        "db_admin.yml")
COMMON = os.path.join(REPO_ROOT, "roles", "common")


def _db_admin(tmp_path, env_text, **extra_vars):
    inst = tmp_path / "inst"
    inst.mkdir()
    (inst / ".env").write_text(env_text)
    out = tmp_path / "out.json"
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {"instance_path": str(inst),
                 "compose_command": "docker compose",
                 "ansible_python_interpreter": sys.executable,
                 **extra_vars},
        "tasks": [
            {"ansible.builtin.include_tasks": DB_ADMIN},
            {"ansible.builtin.copy": {
                "content": "{{ {'service': _db_admin_service,"
                           " 'args': _db_admin_args,"
                           " 'exec': _db_admin_exec} | to_json }}",
                "dest": str(out)}},
        ],
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_LIBRARY=os.path.join(COMMON, "library"),
               ANSIBLE_MODULE_UTILS=os.path.join(COMMON, "module_utils"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env,
                          cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return json.loads(out.read_text())


@pytest.mark.parametrize("env_text, service, args", [
    ("USE_EXTERNAL_DB=true\nMYSQL_HOST=db.example.com\nMYSQL_PORT=3307\n",
     "web",
     '-h "${MYSQL_HOST:-db}" -P "${MYSQL_PORT:-3306}" -u "${MYSQL_USER:-root}"'),
    ("MYSQL_PASSWORD=pw\n", "db", "-u root"),
])
def test_client_arguments(tmp_path, env_text, service, args):
    result = _db_admin(tmp_path, env_text)
    assert {k: result[k] for k in ("service", "args")} == \
        {"service": service, "args": args}
    assert result["exec"].split()[:3] == ["docker", "compose", "exec"]


@pytest.mark.parametrize("env_text, target", [
    ("MYSQL_PASSWORD=pw\n", "statefulset/canasta-inst-db -c db"),
    ("USE_EXTERNAL_DB=true\nMYSQL_HOST=db.example.com\n",
     "deployment/canasta-inst-web -c web"),
])
def test_kubernetes_runs_in_the_pods(tmp_path, env_text, target):
    result = _db_admin(tmp_path, env_text, instance_is_k8s=True,
                       instance_id="inst")
    assert " ".join(result["exec"].split()) == (
        "kubectl exec -i -n canasta-inst %s --" % target)
    # The pod's own environment supplies the password, as on Compose.
    assert result["service"] == ("web" if "EXTERNAL" in env_text else "db")
