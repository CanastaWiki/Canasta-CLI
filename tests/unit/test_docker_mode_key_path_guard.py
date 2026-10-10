"""canasta-docker refuses a gitops init --key it would lose.

The container mounts the host's $HOME, the config directory and the
working directory. A key exported anywhere else is written inside the
container and discarded when the command exits, after init has already
pushed, so the guard refuses it before init starts.
"""
import argparse
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
import canasta  # noqa: E402


@pytest.fixture
def docker_mode(monkeypatch, tmp_path):
    home = tmp_path / "home"
    conf = tmp_path / "conf"
    work = tmp_path / "work"
    for d in (home, conf, work):
        d.mkdir()
    monkeypatch.setenv("CANASTA_RUN_MODE", "docker")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(canasta, "get_config_dir", lambda: str(conf))
    monkeypatch.chdir(work)
    return {"home": home, "conf": conf, "work": work, "tmp": tmp_path}


def _check(key):
    canasta.check_docker_mode_key_path(argparse.Namespace(key=key))


@pytest.mark.parametrize("where", ["home", "conf", "work"])
def test_a_key_in_a_mounted_directory_is_allowed(docker_mode, where):
    _check(str(docker_mode[where] / "gitops.key"))


def test_a_tilde_path_is_expanded(docker_mode):
    _check("~/keys/gitops.key")


def test_a_relative_path_resolves_against_the_working_directory(docker_mode):
    _check("keys/gitops.key")


def test_a_key_outside_every_mount_is_refused(docker_mode, capsys):
    with pytest.raises(SystemExit) as e:
        _check(str(docker_mode["tmp"] / "elsewhere" / "gitops.key"))
    assert e.value.code == 1
    assert "--key ~/canasta-gitops.key" in capsys.readouterr().err


def test_a_sibling_whose_name_extends_a_mount_is_refused(docker_mode):
    # /x/home-other is not under /x/home.
    with pytest.raises(SystemExit):
        _check(str(docker_mode["tmp"] / "home-other" / "gitops.key"))


def test_native_mode_is_never_refused(docker_mode, monkeypatch):
    monkeypatch.setenv("CANASTA_RUN_MODE", "native")
    _check(str(docker_mode["tmp"] / "elsewhere" / "gitops.key"))


def test_no_key_is_left_to_the_command(docker_mode):
    _check(None)


def test_main_runs_the_guard_for_gitops_init():
    with open(canasta.__file__) as f:
        source = f.read()
    assert ('if command_name == "gitops_init":\n'
            '        check_docker_mode_key_path(args)') in source
