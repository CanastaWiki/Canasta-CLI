"""Sidecars cannot reach outside the instance.

config/sidecars.yaml is rendered on every start, whoever wrote it, and its
paths become Compose bind mounts, build contexts, and inlined k8s files.
Mount paths must be container paths and every host path must resolve inside
the instance directory.
"""

import os

import pytest
import yaml

import canasta_render_sidecars
import canasta_sidecar_render as render
from mock_ansible import run_module_with_params


def _base(**extra):
    sidecar = {"name": "helper", "image": "example/helper:1.0"}
    sidecar.update(extra)
    return [sidecar]


@pytest.mark.parametrize("sidecars", [
    _base(),
    _base(volumes=[{"name": "data", "mountPath": "/data", "persistent": True},
                   {"name": "tmp", "mountPath": "/tmp/work"}]),
    _base(files=[{"source": "config/helper.conf", "mountPath": "/etc/helper.conf"}]),
    [{"name": "built", "build": "sidecars/built"}],
    [{"name": "built", "build": {"context": "sidecars/built", "dockerfile": "Dockerfile"}}],
])
def test_sidecars_inside_the_instance_are_accepted(tmp_dir, sidecars):
    assert render.validate_host_access(sidecars, tmp_dir) is None


@pytest.mark.parametrize("sidecars", [
    _base(volumes=[{"name": "root", "mountPath": "/:/host"}]),
    _base(volumes=[{"name": "sock", "mountPath": "/var/run/docker.sock:/var/run/docker.sock"}]),
    _base(volumes=[{"name": "rel", "mountPath": "data"}]),
    _base(volumes=[{"name": "../x", "mountPath": "/data", "persistent": True}]),
    _base(files=[{"source": "../../etc/passwd", "mountPath": "/x"}]),
    _base(files=[{"source": "/etc/passwd", "mountPath": "/x"}]),
    _base(files=[{"source": "config/../../..", "mountPath": "/x", "readOnly": False}]),
    _base(files=[{"source": "config/a.conf", "mountPath": "/x:/y"}]),
    _base(files=[{"source": "~/.ssh/id_ed25519", "mountPath": "/x"}]),
    [{"name": "built", "build": "/"}],
    [{"name": "built", "build": "../.."}],
    [{"name": "built", "build": {"context": ".", "dockerfile": "../../Dockerfile"}}],
])
def test_escapes_are_refused(tmp_dir, sidecars):
    assert render.validate_host_access(sidecars, tmp_dir) is not None


def test_symlink_out_of_the_instance_is_refused(tmp_dir, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(str(outside), os.path.join(tmp_dir, "escape"))
    sidecars = _base(files=[{"source": "escape/secret", "mountPath": "/x"}])
    assert render.validate_host_access(sidecars, tmp_dir) is not None


def test_module_refuses_and_writes_nothing(tmp_dir):
    os.makedirs(os.path.join(tmp_dir, "config"))
    with open(os.path.join(tmp_dir, "config", "sidecars.yaml"), "w") as handle:
        yaml.safe_dump({"sidecars": _base(
            volumes=[{"name": "root", "mountPath": "/:/host"}])}, handle)
    result, failed, msg = run_module_with_params(canasta_render_sidecars, {
        "instance_path": tmp_dir, "orchestrator": "compose"})
    assert failed
    assert "Refusing config/sidecars.yaml" in msg
    assert not os.path.exists(os.path.join(tmp_dir, "docker-compose.sidecars.yml"))


def test_restart_validates_sidecars_before_stopping():
    path = os.path.join(os.path.dirname(__file__), "..", "..", "roles",
                        "instance_lifecycle", "tasks", "restart.yml")
    with open(path) as handle:
        tasks = yaml.safe_load(handle)
    names = [t.get("name") for t in tasks]
    check = tasks[names.index("Check config/sidecars.yaml before stopping")]
    assert check["check_mode"] is True
    assert check["canasta_render_sidecars"]["instance_path"] == "{{ instance_path }}"
    assert names.index("Check config/sidecars.yaml before stopping") < names.index(
        "Stop containers")
