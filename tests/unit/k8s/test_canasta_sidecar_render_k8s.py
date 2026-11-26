"""Sidecar rendering into Helm values for Kubernetes.

Covers render_k8s_values (env and command resolution, inlined files,
envSecret keys carried for secretKeyRef, the original memory quantity) and
the module writing values-sidecars.yaml, plus the env bridge, on Kubernetes.
"""

import os

import yaml

import canasta_render_sidecars
import canasta_sidecar_render as render
from mock_ansible import run_module_with_params
from test_canasta_sidecar_render import (
    CACHE, CITATION, LINKGEN, SECRET_RE, _write_sidecars)


class TestMemoryConversion:
    def test_k8s_render_keeps_k8s_notation(self):
        # Only the Compose path converts; k8s/Helm wants the original quantity.
        out = render.render_k8s_values([CITATION], {}, lambda s: "")
        assert out[0]["resources"]["memory"] == "512Mi"


class TestK8sValues:
    def test_env_and_command_resolved(self):
        env = {"REDIS_MAX_MEMORY": "256mb", "CITATION_SHARED_SECRET": "s3cr3t"}
        out = render.render_k8s_values([CACHE, CITATION], env, lambda s: "FILE")
        cache = next(s for s in out if s["name"] == "cache")
        citation = next(s for s in out if s["name"] == "citation")
        assert "256mb" in cache["command"]
        assert citation["env"]["SHARED_SECRET"] == "s3cr3t"

    def test_list_command_resolved_per_element(self):
        sc = {"name": "x", "image": "i",
              "command": ["serve", "--mem", "${MEM:-100mb}",
                          "--token", "${TOK}"]}
        item = render.render_k8s_values([sc], {"TOK": "t0k"}, lambda s: "")[0]
        assert item["command"] == ["serve", "--mem", "100mb",
                                   "--token", "t0k"]

    def test_file_content_inlined(self):
        out = render.render_k8s_values([CITATION], {}, lambda s: "FILE-BODY")
        assert out[0]["files"][0]["content"] == "FILE-BODY"
        assert out[0]["files"][0]["mountPath"] == "/srv/config.yaml"

    def test_empty(self):
        assert render.render_k8s_values([], {}, lambda s: "") == []


# A sidecar sourcing one env key from the app Secret, one plain.
SECRETSC = {"name": "gen", "image": "x:1",
            "env": {"OP_TOKEN": "${OP_SERVICE_ACCOUNT_TOKEN}",
                    "PLAIN": "${PLAIN_VAL}"},
            "envSecret": ["OP_TOKEN"]}


class TestK8sEnvSecret:
    def test_secret_key_not_resolved_and_listed(self):
        env = {"OP_SERVICE_ACCOUNT_TOKEN": "tok", "PLAIN_VAL": "pv"}
        item = render.render_k8s_values([SECRETSC], env, lambda s: "")[0]
        # The secret key is withheld from the cleartext env map...
        assert "OP_TOKEN" not in item.get("env", {})
        # ...the plain key is still resolved as before...
        assert item["env"]["PLAIN"] == "pv"
        # ...and the secret key is carried as envSecret for secretKeyRef.
        assert item["envSecret"] == ["OP_TOKEN"]

    def test_all_keys_secret_drops_cleartext_env(self):
        sc = {"name": "g", "image": "x:1",
              "env": {"OP_TOKEN": "${T}"}, "envSecret": ["OP_TOKEN"]}
        item = render.render_k8s_values([sc], {"T": "v"}, lambda s: "")[0]
        assert "env" not in item
        assert item["envSecret"] == ["OP_TOKEN"]

    def test_no_envsecret_is_byte_identical(self):
        # Backward compat: a sidecar without envSecret renders exactly as before
        # (no envSecret key, env fully resolved).
        env = {"REDIS_MAX_MEMORY": "256mb", "CITATION_SHARED_SECRET": "s3cr3t"}
        item = render.render_k8s_values([CITATION], env, lambda s: "FILE")[0]
        assert "envSecret" not in item
        assert item["env"] == {"SHARED_SECRET": "s3cr3t"}


class TestEnvPrivate:
    def test_private_still_delivered_to_sidecar(self):
        # The sidecar itself must still receive the private key, and
        # envPrivate must never leak into a runtime artifact.
        k8s = render.render_k8s_values(
            [LINKGEN], {"OP_SERVICE_ACCOUNT_TOKEN": "tok"}, lambda s: "")[0]
        assert k8s["env"]["OP_TOKEN"] == "tok"
        assert "envPrivate" not in k8s


class TestModuleBridge:
    def test_writes_bridge_fragment(self, tmp_dir):
        with open(os.path.join(tmp_dir, ".env"), "w") as handle:
            handle.write("CITATION_HOST=citoid\n")
        _write_sidecars(tmp_dir, [{"name": "citation",
                                   "image": "example/citoid:1.0",
                                   "env": {"HOST": "${CITATION_HOST:-}"}}])
        result, failed, _ = run_module_with_params(canasta_render_sidecars, {
            "instance_path": tmp_dir, "orchestrator": "kubernetes",
            "secret_key_regex": SECRET_RE})
        assert not failed and result["changed"] is True
        bridge = os.path.join(tmp_dir, "config", "settings", "global",
                              "00-canasta-sidecar-env.php")
        with open(bridge) as handle:
            assert "putenv('CITATION_HOST=citoid');" in handle.read()


class TestModuleK8s:
    def test_writes_values_with_resolved_env(self, tmp_dir):
        with open(os.path.join(tmp_dir, ".env"), "w") as handle:
            handle.write("REDIS_MAX_MEMORY=256mb\n")
        _write_sidecars(tmp_dir, [CACHE])
        result, failed, _ = run_module_with_params(canasta_render_sidecars, {
            "instance_path": tmp_dir, "orchestrator": "kubernetes",
            "secret_key_regex": SECRET_RE})
        assert not failed
        with open(os.path.join(tmp_dir, "values-sidecars.yaml")) as handle:
            data = yaml.safe_load(handle)
        assert "256mb" in data["sidecars"][0]["command"]

    def test_empty_writes_empty_list(self, tmp_dir):
        _write_sidecars(tmp_dir, [])
        run_module_with_params(canasta_render_sidecars, {
            "instance_path": tmp_dir, "orchestrator": "kubernetes",
            "secret_key_regex": SECRET_RE})
        path = os.path.join(tmp_dir, "values-sidecars.yaml")
        with open(path) as handle:
            data = yaml.safe_load(handle)
        assert data["sidecars"] == []
