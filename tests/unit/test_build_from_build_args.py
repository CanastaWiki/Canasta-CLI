"""Structural guards for --build-arg passthrough on `canasta create
--build-from` (issue #905).

A real image build needs Docker, which unit CI can't assume, so these lock
in the wiring instead:

  - `create` exposes a repeatable --build-arg;
  - the args are validated (KEY=VALUE) and forwarded to BOTH the CanastaBase
    and Canasta `docker build` invocations;
  - a user-supplied BASE_IMAGE wins over the auto-detected base (user args
    are appended after the built-in --build-arg, and docker takes the last);
  - the args are persisted in the registry and replayed on upgrade, so a
    rebuild doesn't silently revert to defaults.
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
BUILD_FROM = os.path.join(
    REPO_ROOT, "roles", "imagebuild", "tasks", "build_from_source.yml")
CMD_DEFS = os.path.join(REPO_ROOT, "meta", "command_definitions.yml")
REGISTRY = os.path.join(
    REPO_ROOT, "roles", "common", "library", "canasta_registry.py")
REGISTER = os.path.join(REPO_ROOT, "roles", "create", "tasks", "_register.yml")
UPGRADE_MAIN = os.path.join(REPO_ROOT, "roles", "upgrade", "tasks", "main.yml")


def _text(path):
    with open(path) as f:
        return f.read()


def test_create_exposes_repeatable_build_arg():
    with open(CMD_DEFS) as f:
        defs = yaml.safe_load(f)
    create = next(c for c in defs["commands"] if c["name"] == "create")
    ba = next(p for p in create["parameters"] if p["name"] == "build_arg")
    assert ba.get("multi") is True


def test_build_from_validates_key_value():
    txt = _text(BUILD_FROM)
    # Rejects a bare token with no '='.
    assert "'=' not in item" in txt


def test_build_args_forwarded_to_canasta_base_build():
    txt = _text(BUILD_FROM)
    assert "docker build {{ _build_arg_flags }} -t canasta-base:local" in txt


def test_build_args_forwarded_to_canasta_build_after_base_image():
    # user args come AFTER --build-arg BASE_IMAGE so a user BASE_IMAGE wins
    # (docker uses the last value for a repeated key).
    txt = _text(BUILD_FROM)
    assert ("--build-arg BASE_IMAGE={{ _base_image_arg }} "
            "{{ _build_arg_flags }} -t canasta:local") in txt


def test_registry_persists_build_args():
    txt = _text(REGISTRY)
    assert "build_args=dict(type=\"list\"" in txt
    assert "\"buildArgs\"" in txt


def test_register_passes_build_args():
    txt = _text(REGISTER)
    assert "build_args:" in txt


def test_upgrade_replays_build_args():
    txt = _text(UPGRADE_MAIN)
    assert "buildArgs" in txt
