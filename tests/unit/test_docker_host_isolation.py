"""Tests for the conftest guard that keeps DOCKER_HOST from leaking between tests."""

import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from conftest import preserve_docker_host  # noqa: E402
from direct_commands import _helpers  # noqa: E402


def _resolve_with_docker_host(monkeypatch, docker_host):
    monkeypatch.setattr(
        "canasta.resolve_instance",
        lambda _id: {"id": "mysite", "dockerHost": docker_host})
    _helpers._resolve_instance(SimpleNamespace(id="mysite"))


def test_unset_docker_host_is_removed_again(monkeypatch):
    with preserve_docker_host():
        os.environ.pop("DOCKER_HOST", None)
        with preserve_docker_host():
            _resolve_with_docker_host(
                monkeypatch, "unix:///run/user/1000/docker.sock")
            assert os.environ["DOCKER_HOST"] == (
                "unix:///run/user/1000/docker.sock")
        assert "DOCKER_HOST" not in os.environ


def test_set_docker_host_is_restored(monkeypatch):
    with preserve_docker_host():
        os.environ["DOCKER_HOST"] = "unix:///var/run/docker.sock"
        with preserve_docker_host():
            _resolve_with_docker_host(
                monkeypatch, "unix:///run/user/1000/podman/podman.sock")
        assert os.environ["DOCKER_HOST"] == "unix:///var/run/docker.sock"


def test_docker_host_is_restored_when_the_body_raises():
    with preserve_docker_host():
        os.environ.pop("DOCKER_HOST", None)
        with pytest.raises(RuntimeError):
            with preserve_docker_host():
                os.environ["DOCKER_HOST"] = "unix:///run/user/1000/docker.sock"
                raise RuntimeError("boom")
        assert "DOCKER_HOST" not in os.environ
