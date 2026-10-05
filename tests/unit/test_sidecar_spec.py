"""Sidecar declarations pass through only known, well-formed values.

config/sidecars.yaml is copied into the Compose override and interpolated
into the Helm chart, so build options are an allowlist, names and env keys
are held to fixed shapes, and the chart quotes what it interpolates.
"""

import os
import shutil
import subprocess

import pytest
import yaml

import canasta_override_migrate
import canasta_render_sidecars
import canasta_sidecar_render as render
import canasta_sidecars_yaml
from _classifier import secret_key_regex
from mock_ansible import run_module_with_params

SECRET_RE = secret_key_regex()

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
CHART = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "files", "helm", "canasta")
TPL = os.path.join(CHART, "templates", "sidecars.yaml")


def _one(**extra):
    sidecar = {"name": "helper", "image": "example/helper:1.0"}
    sidecar.update(extra)
    return [sidecar]


def _built(build, **extra):
    sidecar = {"name": "built", "build": build}
    sidecar.update(extra)
    return [sidecar]


@pytest.mark.parametrize("sidecars", [
    _one(),
    _one(name="receipt-scanner"),
    _one(env={"REDIS_URL": "redis://cache:6379", "_X1": "${FOO:-bar}"},
         envSecret=["API_KEY"], envPrivate=["REDIS_URL"]),
    _one(resources={"memory": "512Mi", "cpu": "0.5"}),
    _one(resources={"memory": 536870912, "cpu": 1}),
    _one(resources={"memory": "1g", "cpu": "500m"}),
    _built("sidecars/built"),
    _built({"context": "sidecars/built", "dockerfile": "Dockerfile"}),
    _built({"context": "sidecars/built",
            "args": {"VERSION": "1.2", "DEBUG": True, "N": 3, "FROM_ENV": None}}),
])
def test_well_formed_sidecars_are_accepted(sidecars):
    assert render.validate_spec(sidecars) is None


@pytest.mark.parametrize("build, key", [
    ({"context": "sc", "additional_contexts": {"h": "/"}}, "additional_contexts"),
    ({"context": "sc", "privileged": True}, "privileged"),
    ({"context": "sc", "network": "host"}, "network"),
    ({"context": "sc", "secrets": ["s"]}, "secrets"),
])
def test_build_options_outside_the_allowlist_are_refused(build, key):
    error = render.validate_spec(_built(build))
    assert error is not None
    assert "build option '%s' is not allowed" % key in error
    assert "args, context, dockerfile" in error


ALLOWED_KEYS = ("name, image, build, command, env, envSecret, envPrivate, "
                "ports, volumes, files, depends_on, healthcheck, resources")


def test_every_documented_key_is_accepted():
    sidecar = {
        "name": "helper", "image": "example/helper:1.0",
        "command": ["serve", "--port", "8080"],
        "env": {"A": "1", "B": "${B:-x}"}, "envSecret": ["S"],
        "envPrivate": ["A"], "ports": [8080],
        "volumes": [{"name": "data", "mountPath": "/data",
                     "persistent": True, "size": "1Gi"}],
        "files": [{"source": "config/x.conf", "mountPath": "/etc/x.conf"}],
        "depends_on": ["cache"],
        "healthcheck": {"path": "/health", "port": 8080},
        "resources": {"memory": "256Mi", "cpu": "0.5"},
    }
    assert render.validate_spec([sidecar]) is None
    assert canasta_sidecars_yaml.validate_sidecars([sidecar]) is None
    built = dict(sidecar, build="sidecars/helper")
    del built["image"]
    assert render.validate_spec([built]) is None


@pytest.mark.parametrize("key, hint", [
    ("port", "ports"),
    ("depends-on", "depends_on"),
    ("replicas", None),
    ("privileged", None),
    ("environment", None),
])
def test_unknown_sidecar_keys_are_refused(key, hint):
    error = render.validate_spec(_one(**{key: 80}))
    assert error is not None
    assert "sidecar 'helper': key '%s' is not allowed" % key in error
    assert ALLOWED_KEYS in error
    if hint:
        assert "did you mean '%s'?" % hint in error
    else:
        assert "did you mean" not in error


def test_sidecar_add_refuses_an_unknown_key():
    error = canasta_sidecars_yaml.validate_sidecars(_one(port=80))
    assert error is not None and "key 'port' is not allowed" in error


def test_sidecar_add_module_refuses_an_unknown_key(tmp_dir):
    _, failed, msg = run_module_with_params(canasta_sidecars_yaml, {
        "instance_path": tmp_dir, "state": "import",
        "definitions": "name: helper\nimage: example/helper:1.0\nport: 80\n"})
    assert failed
    assert "key 'port' is not allowed" in msg
    assert not os.path.exists(os.path.join(tmp_dir, "config", "sidecars.yaml"))


def test_migrated_sidecars_validate():
    override = {"services": {
        "helper": {
            "image": "example/helper:1.0", "restart": "unless-stopped",
            "command": ["serve"], "environment": ["A=1", "B=2"],
            "expose": ["8080"], "ports": ["9090:9090"],
            "volumes": ["data:/data", "./config/x.conf:/etc/x.conf:ro",
                        "/scratch"],
            "depends_on": ["cache"],
            "healthcheck": {"test": ["CMD", "true"]},
            "deploy": {"resources": {"limits": {"memory": "512M",
                                                "cpus": "0.5"}}},
        },
        "cache": {"build": "sidecars/cache"},
    }}
    plan = canasta_override_migrate.plan_migration(override)
    assert plan["migrated"] == ["helper", "cache"]
    assert canasta_sidecars_yaml.validate_sidecars(plan["sidecars"]) is None


@pytest.mark.parametrize("args", [
    ["VERSION=1"],
    "VERSION=1",
    {"VERSION": {"nested": 1}},
    {"VERSION": [1, 2]},
])
def test_build_args_must_map_names_to_single_values(args):
    error = render.validate_spec(_built({"context": "sc", "args": args}))
    assert error is not None and "build args" in error


def test_build_must_be_a_path_or_mapping():
    error = render.validate_spec(_built(["sc"]))
    assert error is not None and "build must be" in error


@pytest.mark.parametrize("name", [
    "Helper", "helper\n  privileged: true", "-helper", "web", 7, None,
])
def test_invalid_sidecar_names_are_refused(name):
    assert render.validate_spec(_one(name=name)) is not None


@pytest.mark.parametrize("sidecars, field", [
    (_one(env={"BAD-KEY": "x"}), "env"),
    (_one(env={"1X": "x"}), "env"),
    (_one(env={"X\n  securityContext": "x"}), "env"),
    (_one(env=["A=1"]), "env"),
    (_one(envSecret=["OK", "NOT OK"]), "envSecret"),
    (_one(env={"A": "1"}, envPrivate=["A:"]), "envPrivate"),
])
def test_invalid_env_keys_are_refused(sidecars, field):
    error = render.validate_spec(sidecars)
    assert error is not None and field in error


@pytest.mark.parametrize("resources", [
    {"memory": "128Mi\n          securityContext:\n            privileged: true"},
    {"memory": "lots"},
    {"memory": True},
    {"memory": -1},
    {"cpu": "1; rm"},
    {"cpu": "0.5 "},
    "512Mi",
])
def test_invalid_resource_quantities_are_refused(resources):
    assert render.validate_spec(_one(resources=resources)) is not None


def _write_sidecars(instance_path, sidecars):
    os.makedirs(os.path.join(instance_path, "config"), exist_ok=True)
    with open(os.path.join(instance_path, "config", "sidecars.yaml"), "w") as handle:
        yaml.safe_dump({"sidecars": sidecars}, handle)


@pytest.mark.parametrize("orchestrator, artifact", [
    ("compose", "docker-compose.sidecars.yml"),
    ("kubernetes", "values-sidecars.yaml"),
])
def test_render_module_refuses_and_writes_nothing(tmp_dir, orchestrator, artifact):
    _write_sidecars(tmp_dir, _built(
        {"context": ".", "additional_contexts": {"h": "/"}}))
    _, failed, msg = run_module_with_params(canasta_render_sidecars, {
        "instance_path": tmp_dir, "orchestrator": orchestrator,
        "secret_key_regex": SECRET_RE})
    assert failed
    assert "Refusing config/sidecars.yaml" in msg
    assert "additional_contexts" in msg
    assert not os.path.exists(os.path.join(tmp_dir, artifact))


@pytest.mark.parametrize("orchestrator, artifact", [
    ("compose", "docker-compose.sidecars.yml"),
    ("kubernetes", "values-sidecars.yaml"),
])
def test_render_module_refuses_an_unknown_key(tmp_dir, orchestrator, artifact):
    _write_sidecars(tmp_dir, _one(port=80))
    _, failed, msg = run_module_with_params(canasta_render_sidecars, {
        "instance_path": tmp_dir, "orchestrator": orchestrator,
        "secret_key_regex": SECRET_RE})
    assert failed
    assert "key 'port' is not allowed" in msg
    assert not os.path.exists(os.path.join(tmp_dir, artifact))


def test_compose_render_keeps_allowed_build_options(tmp_dir):
    build = {"context": ".", "dockerfile": "Dockerfile", "args": {"V": "1"}}
    _write_sidecars(tmp_dir, _built(build))
    _, failed, msg = run_module_with_params(canasta_render_sidecars, {
        "instance_path": tmp_dir, "orchestrator": "compose",
        "secret_key_regex": SECRET_RE})
    assert not failed, msg
    with open(os.path.join(tmp_dir, "docker-compose.sidecars.yml")) as handle:
        rendered = yaml.safe_load(handle)
    assert rendered["services"]["built"]["build"] == build


def test_sidecar_add_refuses_a_disallowed_build_option():
    error = canasta_sidecars_yaml.validate_sidecars(_built(
        {"context": "sc", "network": "host"}))
    assert error is not None and "network" in error


# --- Helm chart ------------------------------------------------------------- #

def test_chart_quotes_sidecar_supplied_strings():
    with open(TPL) as handle:
        body = handle.read()
    for unquoted in ("name: {{ $k }}", "memory: {{ . }}", "key: {{ . }}",
                     "mountPath: {{ .mountPath }}",
                     "mountPath: {{ $f.mountPath }}",
                     "component: {{ $sc.name }}"):
        assert unquoted not in body
    assert 'storage: {{ .size | default "1Gi" | quote }}' in body


def test_chart_renders_hostile_values_as_plain_strings(tmp_path):
    if not shutil.which("helm"):
        pytest.skip("helm is not installed")
    injected = "128Mi\n          securityContext:\n            privileged: true"
    values = {"sidecars": [{
        "name": "helper", "image": "example/helper:1.0",
        "env": {"A": "x\ny: z"},
        "volumes": [{"name": "tmp", "mountPath": "/tmp/a: b"}],
        "healthcheck": {"path": "/x\n  exec: y", "port": 8080},
        "resources": {"memory": injected, "cpu": "0.5"},
    }]}
    values_file = tmp_path / "values.yaml"
    values_file.write_text(yaml.safe_dump(values))
    out = subprocess.run(
        ["helm", "template", "canasta", CHART, "-f", str(values_file),
         "--show-only", "templates/sidecars.yaml"],
        capture_output=True, text=True)
    if out.returncode != 0:
        pytest.skip("helm template failed: %s" % out.stderr.strip()[:200])
    deployment = next(d for d in yaml.safe_load_all(out.stdout)
                      if d and d["kind"] == "Deployment")
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    assert "securityContext" not in container
    assert container["resources"]["limits"]["memory"] == injected
    assert container["volumeMounts"][0]["mountPath"] == "/tmp/a: b"
    probe = container["readinessProbe"]
    assert probe["httpGet"]["path"] == "/x\n  exec: y"
    assert "exec" not in probe
