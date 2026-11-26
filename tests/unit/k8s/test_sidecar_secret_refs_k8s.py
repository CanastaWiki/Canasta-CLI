"""Kubernetes refuses a sidecar's plain reference to a secret variable.

Kubernetes resolves a sidecar's ${VAR} into the rendered values, so a plain
reference to a secret variable (one the classifier matches, or a key of
config/secrets.env) is refused unless the env key is taken from the app
Secret (envSecret): at render, and already at `sidecar add` and `sidecar
migrate`. The web env bridge stays tracked in a Kubernetes gitops repo, and
the upgrade's Compose re-render leaves a Kubernetes instance alone.
"""

import os

import pytest
import yaml

import canasta_sidecar_render as render
import canasta_sidecars_yaml
import test_sidecar_secret_refs as shared
from mock_ansible import run_module_with_params
from test_sidecar_secret_refs import (
    BRIDGE, ENV, GITOPS, LINKER, TOKEN, _add, _bridge, _git, _gitops_repo,
    _instance, _migrate, _old_instance, _play, _read, _refresh, _render,
    _upgrade_rerender_task)


class TestKubernetesRefusal:
    def test_env_ref_refused(self, tmp_path):
        inst = _instance(tmp_path, [LINKER], ENV)
        _, failed, msg = _render(inst, "kubernetes")
        assert failed
        assert "sidecar 'linker'" in msg and "DB_PASS" in msg
        assert "MYSQL_PASSWORD" in msg
        assert "canasta config set --secret DB_PASS=VALUE" in msg
        assert "envSecret" in msg
        assert not (inst / "values-sidecars.yaml").exists()

    def test_refused_in_check_mode(self, tmp_path):
        inst = _instance(tmp_path, [LINKER], ENV)
        _, failed, _ = _render(inst, "kubernetes", check_mode=True)
        assert failed

    def test_envsecret_allowed(self, tmp_path):
        sidecar = dict(LINKER, envSecret=["DB_PASS"],
                       env={"DB_PASS": "${MYSQL_PASSWORD}",
                            "UPSTREAM": "${LINKER_HOST}"})
        inst = _instance(tmp_path, [sidecar], ENV)
        _, failed, msg = _render(inst, "kubernetes")
        assert not failed, msg
        values = (inst / "values-sidecars.yaml").read_text()
        assert "dbpw" not in values
        assert "dbpw" not in _bridge(inst)

    def test_envprivate_does_not_exempt(self, tmp_path):
        sidecar = {"name": "x", "image": "x:1", "envPrivate": ["P"],
                   "env": {"P": "${MYSQL_PASSWORD}"}}
        inst = _instance(tmp_path, [sidecar], ENV)
        _, failed, _ = _render(inst, "kubernetes")
        assert failed

    def test_recorded_secret_refused(self, tmp_path):
        sidecar = {"name": "x", "image": "x:1",
                   "env": {"OAUTH": "${OAUTH_CLIENT}"}}
        inst = _instance(tmp_path, [sidecar], {},
                         {"config/secrets.env": "OAUTH_CLIENT=abc\n"})
        _, failed, msg = _render(inst, "kubernetes")
        assert failed and "OAUTH_CLIENT" in msg

    @pytest.mark.parametrize("command", [
        ["run", "--pw=${MYSQL_PASSWORD}"], "run --pw=${MYSQL_PASSWORD}"])
    def test_command_ref_refused(self, tmp_path, command):
        sidecar = {"name": "x", "image": "x:1", "command": command}
        inst = _instance(tmp_path, [sidecar], ENV)
        _, failed, msg = _render(inst, "kubernetes")
        assert failed
        assert "command references the secret variable MYSQL_PASSWORD" in msg

    def test_non_secret_refs_allowed(self, tmp_path):
        sidecar = {"name": "x", "image": "x:1",
                   "command": "serve ${LINKER_HOST}",
                   "env": {"UPSTREAM": "${LINKER_HOST}"}}
        inst = _instance(tmp_path, [sidecar], ENV)
        _, failed, msg = _render(inst, "kubernetes")
        assert not failed, msg


class TestAddRefusal:
    def test_kubernetes_refuses_with_render_message(self, tmp_path):
        inst = _instance(tmp_path, [], {},
                         {"config/secrets.env": "K8S_TOKEN=abc\n"})
        _, failed, msg = _add(inst, [TOKEN], "kubernetes")
        assert failed
        assert msg == render.validate_k8s_secret_refs(
            [TOKEN], lambda name: name == "K8S_TOKEN")
        assert yaml.safe_load(
            (inst / "config" / "sidecars.yaml").read_text()) == {
                "sidecars": []}
        _, failed, render_msg = _render(inst, "kubernetes")
        assert not failed, render_msg

    def test_kubernetes_refuses_classified_secret(self, tmp_path):
        inst = _instance(tmp_path, [], ENV)
        _, failed, msg = _add(inst, [LINKER], "kubernetes")
        assert failed and "MYSQL_PASSWORD" in msg

    def test_kubernetes_allows_envsecret(self, tmp_path):
        inst = _instance(tmp_path, [], {},
                         {"config/secrets.env": "K8S_TOKEN=abc\n"})
        sidecar = dict(TOKEN, envSecret=["TOKEN"])
        result, failed, msg = _add(inst, [sidecar], "kubernetes")
        assert not failed, msg
        assert result["names"] == ["x"]

    def test_kubernetes_requires_classifier(self, tmp_path):
        inst = _instance(tmp_path, [], {})
        _, failed, msg = run_module_with_params(canasta_sidecars_yaml, {
            "instance_path": str(inst), "state": "import",
            "definitions": yaml.safe_dump([TOKEN]),
            "orchestrator": "kubernetes", "secret_key_regex": None})
        assert failed and "secret_key_regex" in msg


class TestMigrateRefusal:
    SERVICES = shared.TestMigrateRefusal.SERVICES

    @pytest.mark.parametrize("dry_run", [False, True])
    def test_kubernetes_refuses(self, tmp_path, dry_run):
        inst = _instance(tmp_path, [], ENV)
        _, failed, msg = _migrate(inst, self.SERVICES, "kubernetes", dry_run)
        assert failed
        assert "sidecar 'linker'" in msg and "MYSQL_PASSWORD" in msg
        assert "linker" in (inst / "docker-compose.override.yml").read_text()
        assert "linker" not in (inst / "config" / "sidecars.yaml").read_text()


def test_validate_is_pure():
    def is_secret(name):
        return name == "S"
    assert render.validate_k8s_secret_refs(
        [{"name": "a", "env": {"K": "${S}"}, "envSecret": ["K"]}],
        is_secret) is None
    assert render.validate_k8s_secret_refs(
        [{"name": "a", "env": {"K": "x-${S:-d}"}}], is_secret) is not None


def test_upgrade_rerender_skips_kubernetes(tmp_path):
    inst = _old_instance(tmp_path)
    proc = _play(tmp_path, inst, [
        {"ansible.builtin.set_fact": {"_restart_needed": False,
                                      "_was_running": True}},
        _upgrade_rerender_task()], orchestrator="kubernetes")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "dbpw" in _bridge(inst)
    assert not (inst / "values-sidecars.yaml").exists()
    assert not (inst / "docker-compose.web-env.yml").exists()


def test_kubernetes_init_leaves_bridge_unignored():
    assert "gitignore.compose" not in _read(
        os.path.join(GITOPS, "tasks", "init_kubernetes.yml"))


def test_refresh_leaves_kubernetes_tracking(tmp_path):
    inst = _gitops_repo(tmp_path)
    proc = _refresh(tmp_path, inst, "kubernetes")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert BRIDGE not in (inst / ".gitignore").read_text()
    assert _git(inst, "ls-files", BRIDGE).strip() == BRIDGE
    assert _git(inst, "diff", "--cached", "--name-only") == ""
