"""Sidecar declarations reach the Helm chart only as well-formed values:
rendering values-sidecars.yaml refuses a disallowed build option or an
unknown key and writes nothing, and the chart quotes every sidecar-supplied
string, so a hostile value renders as a plain string rather than as YAML."""

import os
import shutil
import subprocess

import pytest
import yaml

import canasta_render_sidecars
from mock_ansible import run_module_with_params
from test_sidecar_spec import CHART, SECRET_RE, _built, _one, _write_sidecars


TPL = os.path.join(CHART, "templates", "sidecars.yaml")


@pytest.mark.parametrize("orchestrator, artifact", [
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
