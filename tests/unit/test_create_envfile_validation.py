"""create checks its -e env file's settings as config set checks them.

The real _validate_envfile.yml runs here against an env file, with the
canasta_minimal callback, so the messages are the ones an operator sees.
A key config set does not know is written as is, with a warning.
"""

import os
import subprocess
import sys

import pytest
import yaml

from _orchestrator import INSTANCE_IS_K8S_VARS

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
COMMON = os.path.join(REPO_ROOT, "roles", "common")
CREATE_TASKS = os.path.join(REPO_ROOT, "roles", "create", "tasks")
CONFIG_TASKS = os.path.join(REPO_ROOT, "roles", "config", "tasks")
VALIDATE_ENVFILE = os.path.join(CREATE_TASKS, "_validate_envfile.yml")

SECRET = "s3cret-Pw-1234"


def _env():
    env = {k: v for k, v in os.environ.items() if not k.startswith("ANSIBLE_")}
    env.update(ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_STDOUT_CALLBACK="canasta_minimal",
               ANSIBLE_CALLBACK_PLUGINS=os.path.join(REPO_ROOT, "callback_plugins"),
               ANSIBLE_ROLES_PATH=os.path.join(REPO_ROOT, "roles"),
               ANSIBLE_FILTER_PLUGINS=os.path.join(REPO_ROOT, "filter_plugins"),
               ANSIBLE_LIBRARY=os.path.join(COMMON, "library"),
               ANSIBLE_MODULE_UTILS=os.path.join(COMMON, "module_utils"))
    return env


def _play(tmp_path, tasks, play_vars):
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars_files": [os.path.join(REPO_ROOT, "vars", "secret_classification.yml")],
        "vars": dict(INSTANCE_IS_K8S_VARS, canasta_root=REPO_ROOT,
                     ansible_python_interpreter=sys.executable, **play_vars),
        "tasks": tasks,
    }]))
    env = _env()
    env["ANSIBLE_HOME"] = str(tmp_path / "ansible-home")
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run(
        [playbook, "-i", "localhost,canasta_controller,", "-l", "localhost", str(play)],
        capture_output=True, text=True, env=env, cwd=tmp_path, stdin=subprocess.DEVNULL)
    return proc.returncode, proc.stdout + proc.stderr


def _create(tmp_path, lines, orchestrator="compose"):
    envfile = tmp_path / "custom.env"
    envfile.write_text("".join(line + "\n" for line in lines))
    return _play(tmp_path, [{"ansible.builtin.include_tasks": VALIDATE_ENVFILE}],
                 {"envfile": str(envfile), "orchestrator": orchestrator})


@pytest.mark.parametrize("line", [
    "TLS_MODE=acme", "TLS_MODE=\"internal\"", "TLS_MODE=off\r",
    "HTTP_PORT=8080", "HTTPS_PORT=8443",
    "TRUSTED_PROXIES=cloudflare", "TRUSTED_PROXIES=imperva@203.0.113.10/32",
    "TLS_MODE_HOSTS=origin.example.net=acme",
    "CANASTA_ELASTICSEARCH_HEAP=768m",
    "MW_SITEMAP_URL_PATH=/wiki/sitemap",
    "CADDY_TLS_MODE=internal", "CADDY_AUTO_HTTPS=off",
])
def test_good_values_pass(tmp_path, line):
    code, out = _create(tmp_path, [line])
    assert code == 0, out
    assert "Warning:" not in out, out


@pytest.mark.parametrize("line,message", [
    ("TLS_MODE=acmee", "TLS_MODE must be 'acme', 'acme-dns', 'internal', 'custom' or 'off'"),
    ("HTTPS_PORT=44x", "HTTPS_PORT must be a number (got '44x')"),
    ("HTTP_PORT=70000", "HTTP_PORT must be between 1 and 65535 (got '70000')"),
    ("TRUSTED_PROXIES=cloudfalre", "TRUSTED_PROXIES must be"),
    ("TLS_MODE_HOSTS=origin.example.net=letsencrypt", "TLS_MODE_HOSTS must be"),
    ("CANASTA_ELASTICSEARCH_HEAP=4gb", "CANASTA_ELASTICSEARCH_HEAP must be"),
    ("MW_SITEMAP_URL_PATH=/wiki/sitemap/", "MW_SITEMAP_URL_PATH must be a URL path"),
    # An old name is checked under the name create renames it to.
    ("CADDY_TLS_MODE=acmee", "TLS_MODE must be"),
    ("CADDY_TRUSTED_PROXIES=cloudfalre", "TRUSTED_PROXIES must be"),
])
def test_bad_values_fail_with_config_set_messages(tmp_path, line, message):
    code, out = _create(tmp_path, ["MW_SITEMAP_PAUSE_DAYS=1", line])
    assert code != 0, out
    assert message in out, out


@pytest.mark.parametrize("line, key", [
    ("TLS_MODE=acme", "TLS_MODE"), ("TLS_MODE_HOSTS=origin.example.net=acme", "TLS_MODE_HOSTS"),
    ("TLS_CERT=origin.crt", "TLS_CERT"), ("TLS_KEY=origin.key", "TLS_KEY"),
    ("HTTP_PORT=80", "HTTP_PORT"), ("HTTPS_PORT=443", "HTTPS_PORT"),
    ("CADDY_TLS_MODE=internal", "TLS_MODE"),
])
def test_compose_only_keys_are_warned_about_on_kubernetes(tmp_path, line, key):
    code, out = _create(tmp_path, [line], "kubernetes")
    assert code == 0, out
    assert ("Warning: %s does not apply to a Kubernetes instance;"
            " it is written to .env as is." % key) in out, out
    code, out = _create(tmp_path, [line], "compose")
    assert code == 0, out
    assert "does not apply to a Kubernetes instance" not in out, out


def test_an_env_file_shared_with_compose_creates_on_kubernetes(tmp_path):
    code, out = _create(tmp_path, ["HTTP_PORT=8080", "HTTPS_PORT=8443", "TLS_MODE=off"],
                        "kubernetes")
    assert code == 0, out


@pytest.mark.parametrize("line", ["HTTP_PORT=44x", "TLS_MODE=acmee"])
def test_malformed_compose_only_values_still_fail_on_kubernetes(tmp_path, line):
    code, out = _create(tmp_path, [line], "kubernetes")
    assert code != 0, out


def test_the_cdn_behind_proxy_form_is_refused_on_kubernetes(tmp_path):
    code, out = _create(tmp_path, ["TRUSTED_PROXIES=imperva@203.0.113.10/32"], "k8s")
    assert code != 0 and "Docker Compose only" in out, out
    code, out = _create(tmp_path, ["TRUSTED_PROXIES=cloudflare"], "k8s")
    assert code == 0, out


@pytest.mark.parametrize("orchestrator", ["compose", "kubernetes"])
def test_keys_create_sets_pass_without_a_warning(tmp_path, orchestrator):
    # config set refuses these, or needs --force for them; create takes them.
    code, out = _create(tmp_path, [
        "USE_EXTERNAL_DB=true", "MYSQL_HOST=db.example.com", "MYSQL_PORT=3306",
        "MYSQL_USER=admin", "MYSQL_SSL=false", f"MYSQL_PASSWORD={SECRET}",
        f"MW_SECRET_KEY={SECRET}", f"WIKI_DB_PASSWORD={SECRET}",
        "CANASTA_STAGING_CERTS=true", f"SMTP_PASSWORD={SECRET}",
    ], orchestrator)
    assert code == 0, out
    assert "Warning:" not in out, out
    assert SECRET not in out, out


def test_an_unknown_key_is_warned_about_once(tmp_path):
    code, out = _create(tmp_path, [
        "CANASTA_ENABLE_ELASTICSERACH=true", f"MY_EXTENSION_TOKEN={SECRET}",
        "CANASTA_ENABLE_ELASTICSERACH=false", "# COMMENTED_OUT=1", "lowercase_key=1",
    ])
    assert code == 0, out
    warnings = [line for line in out.splitlines() if line.startswith("Warning:")]
    assert warnings == [
        "Warning: CANASTA_ENABLE_ELASTICSERACH is not a known configuration key;"
        " it is written to .env as is.",
        "Warning: MY_EXTENSION_TOKEN is not a known configuration key;"
        " it is written to .env as is.",
    ], out
    assert SECRET not in out, out


def test_host_gateway_passes_without_a_warning(tmp_path):
    code, out = _create(tmp_path, ["HOST_GATEWAY=10.88.0.1"])
    assert code == 0, out
    assert "Warning:" not in out, out


def test_compose_profiles_is_reported_as_not_used(tmp_path):
    code, out = _create(tmp_path, ["COMPOSE_PROFILES=elasticsearch"])
    assert code == 0, out
    warnings = [line for line in out.splitlines() if line.startswith("Warning:")]
    assert warnings == [
        "Warning: create sets COMPOSE_PROFILES itself; the env file's value is not used.",
    ], out


def test_an_env_file_of_known_keys_passes_quietly(tmp_path):
    code, out = _create(tmp_path, ["CANASTA_ENABLE_ELASTICSEARCH=true", "",
                                   "MW_SITE_FQDN=wiki.example.com"])
    assert code == 0, out
    assert "Warning:" not in out, out


def _tasks(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _names(tasks):
    return [t.get("name") for t in tasks]


def test_create_validates_before_it_writes():
    inputs = _tasks(os.path.join(CREATE_TASKS, "_validate_inputs.yml"))
    include = next(t for t in inputs
                   if t.get("ansible.builtin.include_tasks") == "_validate_envfile.yml")
    # Before the Kubernetes setup, which installs Argo CD.
    assert inputs.index(include) < _names(inputs).index(
        "Kubernetes setup (validate + stale-state check + Argo CD)")
    main = _names(_tasks(os.path.join(CREATE_TASKS, "main.yml")))
    assert main.index("Validate inputs and check for stale state") < main.index(
        "Create instance directory")


def test_config_set_and_create_share_the_value_checks():
    validate_key = _tasks(os.path.join(CONFIG_TASKS, "_validate_key.yml"))
    assert any(t.get("ansible.builtin.include_tasks") == "_validate_value.yml"
               for t in validate_key)
    side_effects = _names(_tasks(os.path.join(CONFIG_TASKS, "_side_effects.yml")))
    for name in ("Validate port is numeric", "Validate TLS_MODE value"):
        assert name not in side_effects


def _config(tmp_path, tasks_from, play_vars):
    inst = tmp_path / "inst"
    inst.mkdir()
    (inst / ".env").write_text("HTTP_PORT=80\nHTTPS_PORT=443\n")
    return _play(tmp_path, [{"ansible.builtin.include_role": {
        "name": "config", "tasks_from": tasks_from}}],
        dict({"instance_path": str(inst), "no_restart": True}, **play_vars))


@pytest.mark.parametrize("setting,message", [
    ("TLS_MODE=acmee", "TLS_MODE must be"),
    ("HTTPS_PORT=44x", "HTTPS_PORT must be a number"),
    ("CANASTA_ENABLE_ELASTICSERACH=true", "Unrecognized key 'CANASTA_ENABLE_ELASTICSERACH'"),
    ("CADDY_TLS_MODE=internal", "was renamed to"),
    ("MYSQL_HOST=db.example.com", "cannot be changed after instance creation"),
])
def test_config_set_still_refuses(tmp_path, setting, message):
    code, out = _config(tmp_path, "set.yml", {"settings": [setting],
                                              "instance_orchestrator": "compose"})
    assert code != 0, out
    assert message in out, out


@pytest.mark.parametrize("setting", ["HTTP_PORT=8080", "MW_SECRET_KEY=x"])
def test_config_set_refuses_on_kubernetes(tmp_path, setting):
    code, out = _config(tmp_path, "set.yml", {"settings": [setting],
                                              "instance_orchestrator": "kubernetes"})
    assert code != 0, out
    assert "cannot be set" in out and "Kubernetes instance" in out, out


def test_config_unset_of_a_port_still_works(tmp_path):
    validate_key = os.path.join(CONFIG_TASKS, "_validate_key.yml")
    code, out = _play(tmp_path, [{"ansible.builtin.include_tasks": validate_key}], {
        "_config_key": "HTTPS_PORT", "instance_orchestrator": "compose",
        "canasta_known_keys": [{"name": "HTTPS_PORT"}],
        "canasta_create_only_keys": []})
    assert code == 0, out
