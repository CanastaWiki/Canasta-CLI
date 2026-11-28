"""`canasta upgrade` refuses to move an instance from an earlier line.

From 5.0.0 on, the CLI and the Canasta images share a version, so an
instance on a release image of a lower major is on an earlier line. Moving
it is the major upgrade, and only the earlier major's CLI runs that
upgrade's checks and backups (`upgrade --allow-major`). A CLI that got
here another way, such as an old Docker wrapper that crossed the major on
a plain `canasta upgrade`, stops before any instance moves.
"""

import json

import pytest

import canasta


@pytest.fixture
def setup(tmp_path, monkeypatch):
    script = tmp_path / "cli"
    script.mkdir()
    (script / "VERSION").write_text("5.0.0\n")
    monkeypatch.setattr(canasta, "SCRIPT_DIR", str(script))
    config = tmp_path / "config"
    config.mkdir()
    monkeypatch.setenv("CANASTA_CONFIG_DIR", str(config))

    def register(**images):
        instances = {}
        for iid, image in images.items():
            path = tmp_path / iid
            path.mkdir()
            (path / ".env").write_text("CANASTA_IMAGE=%s\n" % image)
            instances[iid] = {"path": str(path), "host": "localhost"}
        (config / "conf.json").write_text(json.dumps({"Instances": instances}))

    return register, config


def _check():
    try:
        canasta.check_earlier_line_instances()
    except SystemExit as e:
        return e.code
    return 0


def test_an_earlier_line_instance_stops_the_upgrade(setup, capsys):
    register, _ = setup
    register(old="ghcr.io/canastawiki/canasta:3.6.2",
             new="ghcr.io/canastawiki/canasta:5.0.0")
    assert _check() == 1
    err = capsys.readouterr().err
    assert "old: ghcr.io/canastawiki/canasta:3.6.2" in err
    assert "new:" not in err
    assert "latest 4.x release" in err and "--allow-major" in err


def test_a_canasta_base_pin_counts(setup):
    register, _ = setup
    register(base="ghcr.io/canastawiki/canasta-base:3.6.0")
    assert _check() == 1


def test_the_recorded_checks_let_it_through(setup):
    register, config = setup
    register(old="ghcr.io/canastawiki/canasta:3.6.2")
    (config / canasta.MAJOR_MOVE_FILE).write_text("5\n")
    assert _check() == 0


def test_checks_recorded_for_another_major_do_not(setup):
    register, config = setup
    register(old="ghcr.io/canastawiki/canasta:3.6.2")
    (config / canasta.MAJOR_MOVE_FILE).write_text("4\n")
    assert _check() == 1


@pytest.mark.parametrize("image", [
    "ghcr.io/canastawiki/canasta:5.0.0-rc.1",
    "ghcr.io/canastawiki/canasta:5.1.0",
    "example.com/mywiki:3.0.0",
    "",
])
def test_anything_else_is_left_alone(setup, image):
    register, _ = setup
    register(site=image)
    assert _check() == 0
