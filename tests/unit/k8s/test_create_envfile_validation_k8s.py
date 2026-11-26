"""create's -e env file check on a Kubernetes instance: Compose-only keys are
written with a warning, malformed values still fail, and the CDN form of
TRUSTED_PROXIES is refused; config set refuses the same keys there. The
check also runs before create's Kubernetes setup installs Argo CD."""

import os

import pytest

from test_create_envfile_validation import (
    CREATE_TASKS, SECRET, _config, _create, _names, _tasks)


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


def test_create_validates_before_the_kubernetes_setup():
    inputs = _tasks(os.path.join(CREATE_TASKS, "_validate_inputs.yml"))
    include = next(t for t in inputs
                   if t.get("ansible.builtin.include_tasks") == "_validate_envfile.yml")
    # Before the Kubernetes setup, which installs Argo CD.
    assert inputs.index(include) < _names(inputs).index(
        "Kubernetes setup (validate + stale-state check + Argo CD)")


@pytest.mark.parametrize("setting", ["HTTP_PORT=8080", "MW_SECRET_KEY=x"])
def test_config_set_refuses_on_kubernetes(tmp_path, setting):
    code, out = _config(tmp_path, "set.yml", {"settings": [setting],
                                              "instance_orchestrator": "kubernetes"})
    assert code != 0, out
    assert "cannot be set" in out and "Kubernetes instance" in out, out


@pytest.mark.parametrize("orchestrator", ["kubernetes"])
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
