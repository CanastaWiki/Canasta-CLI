"""_read_gitops_host.yml reads this host's gitops identity and vars.

It is the one reader of .gitops-host and the host and shared vars.yaml on
the working tree, so each case is run as a real playbook on localhost.
"""

import json
import os
import subprocess
import sys

import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
READ = os.path.join(REPO_ROOT, "roles", "gitops", "tasks", "_read_gitops_host.yml")


def _instance(tmp_path, host=None, host_vars=None, shared_vars=None):
    inst = tmp_path / "inst"
    inst.mkdir()
    if host is not None:
        (inst / ".gitops-host").write_text(host + "\n")
    if host_vars is not None:
        (inst / "hosts" / host).mkdir(parents=True)
        (inst / "hosts" / host / "vars.yaml").write_text(host_vars)
    if shared_vars is not None:
        (inst / "hosts" / "_shared").mkdir(parents=True)
        (inst / "hosts" / "_shared" / "vars.yaml").write_text(shared_vars)
    return inst


def _run(tmp_path, **include_vars):
    out = tmp_path / "out.json"
    include = {"ansible.builtin.include_tasks": READ}
    if include_vars:
        include["vars"] = include_vars
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {"instance_path": str(tmp_path / "inst"),
                 "ansible_python_interpreter": sys.executable},
        "tasks": [
            include,
            {"ansible.builtin.copy": {
                "content": "{{ _gitops_host | to_json }}",
                "dest": str(out)}},
        ],
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_LOCAL_TEMP=str(tmp_path / "ansible-tmp"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env, cwd=tmp_path)
    return proc, (json.loads(out.read_text()) if out.exists() else None)


def test_not_a_gitops_instance(tmp_path):
    _instance(tmp_path)
    proc, data = _run(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert data == {"is_gitops": False, "hostname": "",
                    "host_vars": {}, "shared_vars": {}}


def test_reads_host_and_shared_vars(tmp_path):
    _instance(tmp_path, "prod", "mysql_password: s3cret-host\n",
              "aws_key: s3cret-shared\n")
    proc, data = _run(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert data == {"is_gitops": True, "hostname": "prod",
                    "host_vars": {"mysql_password": "s3cret-host"},
                    "shared_vars": {"aws_key": "s3cret-shared"}}
    assert "s3cret" not in proc.stdout + proc.stderr


def test_absent_shared_vars_are_empty(tmp_path):
    _instance(tmp_path, "prod", "a: 1\n")
    proc, data = _run(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert data["host_vars"] == {"a": 1}
    assert data["shared_vars"] == {}


def test_empty_vars_files_parse_to_empty_dicts(tmp_path):
    _instance(tmp_path, "prod", "", "")
    proc, data = _run(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert data["host_vars"] == {}
    assert data["shared_vars"] == {}


def test_missing_host_vars_fail_with_the_path(tmp_path):
    _instance(tmp_path, "prod")
    proc, data = _run(tmp_path)
    assert proc.returncode != 0
    assert data is None
    assert "hosts/prod/vars.yaml" in proc.stdout + proc.stderr


def test_missing_host_vars_are_empty_when_optional(tmp_path):
    _instance(tmp_path, "prod", shared_vars="b: 2\n")
    proc, data = _run(tmp_path, gitops_host_vars_optional=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert data["host_vars"] == {}
    assert data["shared_vars"] == {"b": 2}


def test_require_fails_without_the_marker(tmp_path):
    _instance(tmp_path)
    proc, data = _run(tmp_path, gitops_host_require=True)
    assert proc.returncode != 0
    assert data is None
    assert ".gitops-host" in proc.stdout + proc.stderr


def test_unrequested_vars_are_not_read(tmp_path):
    # Malformed files prove the reads are skipped rather than tolerated.
    _instance(tmp_path, "prod", "a: [unclosed\n", "b: [unclosed\n")
    proc, data = _run(tmp_path, gitops_host_read_host_vars=False,
                      gitops_host_read_shared_vars=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert data == {"is_gitops": True, "hostname": "prod",
                    "host_vars": {}, "shared_vars": {}}


def test_malformed_host_vars_fail(tmp_path):
    _instance(tmp_path, "prod", "a: [unclosed\n")
    proc, data = _run(tmp_path)
    assert proc.returncode != 0
    assert data is None
