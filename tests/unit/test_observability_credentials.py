"""OpenSearch credentials for CANASTA_ENABLE_OBSERVABILITY.

The bcrypt hash is generated on the instance's host, whose /bin/sh may be
dash, so the password reaches python3 on stdin through the command module
rather than a shell here-string, which also keeps it off the command line.

`config set` runs its side effects before writing the new flag to .env, so
the credentials step is told the flag is being enabled rather than reading
the old value and skipping.
"""

import os

import jinja2
import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

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


# Compose interpolates $ in .env values, so the bcrypt hash is stored with
# each $ doubled and undoubled where the Caddyfile is rendered.
HASH = "$2b$12$o9twYopXabcdefghijklmnOPQRSTUVWXYZ0123456789abcdefgh"
ESCAPED = HASH.replace("$", "$$")
REWRITE_CADDY = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "rewrite_caddy.yml")
CADDYFILE_J2 = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "templates", "Caddyfile.j2")


def _template(expr, variables):
    templar = Templar(loader=DataLoader(), variables=variables)
    return templar.template(trust_as_template(expr))


def _caddy_hash_expr():
    facts = _task(REWRITE_CADDY, "Determine Caddyfile parameters")[
        "ansible.builtin.set_fact"]
    return facts["_os_password_hash"]


def test_generated_hash_is_written_escaped():
    value = _task(CREDS, "Set OS_PASSWORD_HASH")["canasta_env"]["value"]
    assert _template(value, {"_bcrypt_result": {"stdout": HASH + "\n"}}) == ESCAPED


def test_existing_unescaped_hash_is_migrated():
    task = _task(CREDS, "Escape $ in an existing OS_PASSWORD_HASH")
    value = task["canasta_env"]["value"]
    for stored in (HASH, ESCAPED):
        env = {"_env_obs": {"variables": {"OS_PASSWORD_HASH": stored}}}
        assert _template(value, env) == ESCAPED


def test_migration_skips_gitops_hosts():
    task = _task(CREDS, "Escape $ in an existing OS_PASSWORD_HASH")
    assert "not _obs_gitops_host.stat.exists" in task["when"]


def test_caddy_reads_escaped_and_legacy_hash():
    expr = _caddy_hash_expr()
    for stored in (ESCAPED, HASH):
        env = {"_env_caddy": {"variables": {"OS_PASSWORD_HASH": stored}}}
        assert _template(expr, env) == HASH


def test_caddyfile_basicauth_holds_the_real_hash():
    with open(CADDYFILE_J2) as f:
        src = f.read()
    stored = _template(
        _caddy_hash_expr(),
        {"_env_caddy": {"variables": {"OS_PASSWORD_HASH": ESCAPED}}})
    env = jinja2.Environment(keep_trailing_newline=True)
    env.filters["bool"] = lambda v: str(v).lower() in ("true", "1", "yes")
    rendered = env.from_string(src).render(
        _site_address="example.com", _backend="web:80", _observable=True,
        _os_user="admin", _os_password_hash=stored, _staging_certs=False)
    assert "admin " + HASH in rendered
    assert "$$" not in rendered
