"""OpenSearch credentials for CANASTA_ENABLE_OBSERVABILITY.

The bcrypt hash is generated on the instance's host, whose /bin/sh may be
dash, so the password reaches python3 on stdin through the command module
rather than a shell here-string, which also keeps it off the command line.

`config set` runs its side effects before writing the new flag to .env, so
the credentials step is told the flag is being enabled rather than reading
the old value and skipping.
"""

import os

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
CREDS = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks",
    "ensure_observability_credentials.yml")
SIDE_EFFECTS = os.path.join(
    REPO_ROOT, "roles", "config", "tasks", "_side_effects.yml")


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _dicts(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _dicts(value)
    elif isinstance(node, list):
        for item in node:
            yield from _dicts(item)


def _task(path, name):
    for task in _dicts(_load(path)):
        if task.get("name") == name:
            return task
    raise AssertionError("task %r not found in %s" % (name, path))


def test_hash_uses_command_with_stdin():
    task = _task(CREDS, "Generate bcrypt hash")
    assert "ansible.builtin.shell" not in task
    assert "shell" not in task
    spec = task["ansible.builtin.command"]
    argv = spec["argv"]
    assert argv[:2] == ["python3", "-c"]
    assert "bcrypt.hashpw" in argv[2]
    assert "sys.stdin" in argv[2]
    assert spec["stdin"] == "{{ _os_pass.value }}"
    assert spec["stdin_add_newline"] is False
    assert task["no_log"] is True


def test_password_not_on_command_line():
    task = _task(CREDS, "Generate bcrypt hash")
    argv = task["ansible.builtin.command"]["argv"]
    assert not any("_os_pass" in arg for arg in argv)
    with open(CREDS) as f:
        assert "<<<" not in f.read()


def test_bcrypt_refusal_names_the_instance_host():
    msg = _task(CREDS, "Refuse to proceed without bcrypt")[
        "ansible.builtin.fail"]["msg"]
    assert "controller" not in msg
    assert "instance's host" in msg
    assert "ansible_host" in msg


def test_probe_and_hash_run_on_the_instance_host():
    for name in ("Probe for Python bcrypt module", "Generate bcrypt hash"):
        assert "delegate_to" not in _task(CREDS, name)


def test_enabling_flag_overrides_stale_env():
    expr = _task(CREDS, "Check if observability is enabled")[
        "ansible.builtin.set_fact"]["_obs_enabled"]
    assert "_obs_enabling" in expr
    assert "CANASTA_ENABLE_OBSERVABILITY" in expr


def test_config_set_side_effect_passes_enabling():
    task = _task(SIDE_EFFECTS, "Apply CANASTA_ENABLE_OBSERVABILITY side effects")
    assert task["ansible.builtin.include_role"]["tasks_from"] == (
        "ensure_observability_credentials.yml")
    assert task["vars"]["_obs_enabling"] is True
