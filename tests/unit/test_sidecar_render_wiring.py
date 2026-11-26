"""Structural guards for wiring the sidecar render into start/stop/delete.

These lock in the integration points that the renderer depends on but that a
unit test of the render logic can't see: start renders the sidecars for
Compose, the generated Compose layer is added as an `-f` (before the user
override), and stop and delete load it too.
"""

import os

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
START = os.path.join(ROOT, "roles", "orchestrator", "tasks", "start.yml")
STOP = os.path.join(ROOT, "roles", "orchestrator", "tasks", "stop.yml")
DESTROY = os.path.join(ROOT, "roles", "orchestrator", "tasks", "destroy.yml")
COMPOSE_FILE_ARGS = os.path.join(
    ROOT, "roles", "orchestrator", "tasks", "compose_file_args.yml")


def _text(path):
    with open(path) as handle:
        return handle.read()


def test_start_renders_sidecars_for_compose():
    body = _text(START)
    assert "canasta_render_sidecars" in body
    assert "orchestrator: compose" in body


def test_compose_layer_added_before_user_override():
    assert "include_tasks: compose_file_args.yml" in _text(START)
    body = _text(COMPOSE_FILE_ARGS)
    # Sidecars layer must precede the override in the -f list so the
    # override wins a service-name clash.
    assert body.index("- docker-compose.sidecars.yml") < body.index(
        "- docker-compose.override.yml")


def test_stop_includes_sidecar_layer_for_teardown():
    assert "include_tasks: compose_file_args.yml" in _text(STOP)
    assert "- docker-compose.sidecars.yml" in _text(COMPOSE_FILE_ARGS)


def test_destroy_removes_sidecar_orphans_and_volumes():
    # delete must remove BOTH the sidecar container (--remove-orphans) and
    # its named volume. The volume is declared only in
    # docker-compose.sidecars.yml, so `down -v` must load that layer or the
    # volume survives and later blocks recreate with a stale-volume error.
    body = _text(DESTROY)
    assert "down -v --remove-orphans" in body
    assert "include_tasks: compose_file_args.yml" in body
