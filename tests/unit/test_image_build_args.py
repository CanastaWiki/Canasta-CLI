"""`canasta image build-args`, `set-build-arg` and `unset-build-arg`.

A --build-from instance keeps its build arguments in its controller
registry record, and every rebuild replays them. These commands show and
change that record and rebuild the image with the result. The playbook
half runs here against a temporary registry with docker stubbed.
"""

import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest
import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "filter_plugins"))

import canasta  # noqa: E402
import direct_commands  # noqa: E402
from canasta_images import (  # noqa: E402
    canasta_build_arg_keys,
    canasta_set_build_args,
    canasta_unset_build_args,
)

APPLY = os.path.join(
    REPO_ROOT, "roles", "imagebuild", "tasks", "apply_build_args.yml")
SET_PLAYBOOK = os.path.join(REPO_ROOT, "playbooks", "image_set_build_arg.yml")
UNSET_PLAYBOOK = os.path.join(
    REPO_ROOT, "playbooks", "image_unset_build_arg.yml")
DEFINITIONS = os.path.join(REPO_ROOT, "meta", "command_definitions.yml")

NEW = "sha256:" + "b" * 64


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------

class TestSetBuildArgs:
    def test_replaces_a_recorded_key_in_place(self):
        assert canasta_set_build_args(
            ["BASE_IMAGE=old", "FOO=1"], ["BASE_IMAGE=new"]
        ) == ["BASE_IMAGE=new", "FOO=1"]

    def test_appends_a_new_key(self):
        assert canasta_set_build_args(["FOO=1"], ["BAR=2"]) == ["FOO=1", "BAR=2"]

    def test_drops_later_duplicates_of_a_set_key(self):
        assert canasta_set_build_args(
            ["FOO=1", "BAR=2", "FOO=3"], ["FOO=4"]
        ) == ["FOO=4", "BAR=2"]

    def test_last_value_given_wins(self):
        assert canasta_set_build_args([], ["FOO=1", "FOO=2"]) == ["FOO=2"]

    def test_value_may_contain_equals_and_be_empty(self):
        assert canasta_set_build_args(
            ["A=x"], ["A=b=c", "E="]) == ["A=b=c", "E="]

    def test_no_recorded_args(self):
        assert canasta_set_build_args(None, ["FOO=1"]) == ["FOO=1"]


class TestUnsetBuildArgs:
    def test_removes_every_entry_for_the_key(self):
        assert canasta_unset_build_args(
            ["FOO=1", "BAR=2", "FOO=3"], ["FOO"]) == ["BAR=2"]

    def test_unknown_key_changes_nothing(self):
        assert canasta_unset_build_args(["FOO=1"], ["BAR"]) == ["FOO=1"]

    def test_key_is_not_a_prefix_match(self):
        assert canasta_unset_build_args(["FOOBAR=1"], ["FOO"]) == ["FOOBAR=1"]

    def test_keys(self):
        assert canasta_build_arg_keys(["A=1", "B=2=3", "C="]) == ["A", "B", "C"]


# ---------------------------------------------------------------------------
# Command definitions and CLI wiring
# ---------------------------------------------------------------------------

def _definition(name):
    with open(DEFINITIONS) as f:
        data = yaml.safe_load(f)
    return next(c for c in data["commands"] if c["name"] == name)


def test_commands_are_in_the_image_group():
    for sub in ("build-args", "set-build-arg", "unset-build-arg"):
        assert sub in canasta.SUBCOMMAND_GROUPS["image"]


@pytest.mark.parametrize("name, positional", [
    ("image_set_build_arg", "settings"),
    ("image_unset_build_arg", "keys"),
])
def test_changes_take_a_list_and_can_skip_the_rebuild(name, positional):
    cmd = _definition(name)
    params = {p["name"]: p for p in cmd["parameters"]}
    assert params[positional]["positional"] and params[positional]["as_list"]
    assert params[positional]["required"]
    assert params["no_rebuild"]["type"] == "bool"
    assert "orchestrators" not in cmd


@pytest.mark.parametrize("name", ["image_set_build_arg", "image_unset_build_arg"])
def test_changes_hold_the_instance_lock(name):
    assert name in canasta.LOCKED_COMMANDS


def test_the_listing_is_direct_only():
    assert _definition("image_build_args")["direct_only"] is True
    assert direct_commands.is_direct_command("image_build_args")


# ---------------------------------------------------------------------------
# canasta image build-args
# ---------------------------------------------------------------------------

def _write_registry(tmp_path, monkeypatch, **fields):
    entry = {"id": "site", "path": str(tmp_path / "site"),
             "orchestrator": "compose"}
    entry.update(fields)
    (tmp_path / "conf.json").write_text(
        json.dumps({"Instances": {"site": entry}}))
    monkeypatch.setenv("CANASTA_CONFIG_DIR", str(tmp_path))


class TestListBuildArgs:
    def test_prints_each_recorded_arg(self, tmp_path, monkeypatch, capsys):
        _write_registry(tmp_path, monkeypatch, buildFrom="/src",
                        buildArgs=["BASE_IMAGE=x:1", "FOO=bar"])
        rc = direct_commands.cmd_image_build_args(SimpleNamespace(id="site"))
        assert rc == 0
        assert capsys.readouterr().out == "BASE_IMAGE=x:1\nFOO=bar\n"

    def test_reports_none_recorded(self, tmp_path, monkeypatch, capsys):
        _write_registry(tmp_path, monkeypatch, buildFrom="/src")
        rc = direct_commands.cmd_image_build_args(SimpleNamespace(id="site"))
        assert rc == 0
        assert "No build arguments recorded for 'site'." in capsys.readouterr().out

    def test_refuses_an_instance_not_built_from_source(
            self, tmp_path, monkeypatch, capsys):
        _write_registry(tmp_path, monkeypatch)
        rc = direct_commands.cmd_image_build_args(SimpleNamespace(id="site"))
        assert rc == 1
        assert "not built from source" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# set-build-arg / unset-build-arg playbooks
# ---------------------------------------------------------------------------

DOCKER = """#!/bin/sh
echo "$*" >> "$STUB_LOG"
case "$1" in
  buildx) exit 1 ;;
  build) exit "${STUB_BUILD_RC:-0}" ;;
  ps) [ -n "$STUB_CONTAINERS" ] && printf '%s\\n' $STUB_CONTAINERS ;;
  inspect) for _ in $STUB_CONTAINERS; do echo "$STUB_RUNNING"; done ;;
  image) echo "$STUB_BUILT" ;;
esac
exit 0
"""


def _run(tmp_path, playbook, extra, record=None, **stub):
    """Run a set/unset playbook with resolution already done.

    Returns (returncode, output, registry record after the run).
    """
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    (bindir / "docker").write_text(DOCKER)
    (bindir / "docker").chmod(0o755)
    src = tmp_path / "src" / "Canasta"
    src.mkdir(parents=True, exist_ok=True)
    (src / "Dockerfile").write_text("FROM scratch\n")
    site = tmp_path / "site"
    site.mkdir(exist_ok=True)
    entry = {"id": "site", "path": str(site), "orchestrator": "compose",
             "buildFrom": str(tmp_path / "src")}
    entry.update(record or {})
    config_dir = tmp_path / "config"
    config_dir.mkdir(exist_ok=True)
    (config_dir / "conf.json").write_text(
        json.dumps({"Instances": {"site": entry}}))

    # resolve_instance.yml is replaced by the facts it sets.
    with open(playbook) as f:
        tasks = [t for t in yaml.safe_load(f)
                 if t.get("name") != "Resolve instance"]
    play_vars = {
        "canasta_root": REPO_ROOT, "instance_is_k8s": False,
        "instance_id": "site", "instance_path": str(site),
        "inspect_command": "docker",
        "canasta_default_image": "ghcr.io/canastawiki/canasta:3.5.29",
        "ansible_python_interpreter": sys.executable,
        "_instance_result": {"instance": entry},
    }
    play_vars.update(extra)
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost", "connection": "local", "gather_facts": False,
        "vars": play_vars,
        "pre_tasks": [{"ansible.builtin.add_host": {
            "name": "canasta_controller", "ansible_connection": "local",
            "ansible_python_interpreter": sys.executable}}],
        "tasks": tasks,
    }]))
    log = tmp_path / "calls.log"
    log.write_text("")
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_CONFIG=os.path.join(REPO_ROOT, "ansible.cfg"),
               CANASTA_CONFIG_DIR=str(config_dir),
               PATH=str(bindir) + os.pathsep + os.environ["PATH"],
               STUB_LOG=str(log))
    env.update({"STUB_" + k.upper(): v for k, v in stub.items()})
    ansible = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([ansible, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env,
                          cwd=REPO_ROOT)
    after = json.loads((config_dir / "conf.json").read_text())
    return proc.returncode, proc.stdout + proc.stderr, after["Instances"]["site"]


def _builds(tmp_path):
    return [line for line in (tmp_path / "calls.log").read_text().splitlines()
            if line.startswith("build ")]


class TestSet:
    def test_rebuilds_then_records_args_and_base(self, tmp_path):
        rc, out, entry = _run(
            tmp_path, SET_PLAYBOOK,
            {"settings": ["BASE_IMAGE=ghcr.io/canastawiki/canasta:3.5.29"]},
            record={"buildArgs": ["BASE_IMAGE=ghcr.io/canastawiki/canasta:3.5.28",
                                  "FOO=1"]},
            built=NEW)
        assert rc == 0, out
        assert entry["buildArgs"] == [
            "BASE_IMAGE=ghcr.io/canastawiki/canasta:3.5.29", "FOO=1"]
        assert entry["buildBase"] == "ghcr.io/canastawiki/canasta:3.5.29"
        (build,) = _builds(tmp_path)
        assert "--build-arg BASE_IMAGE=ghcr.io/canastawiki/canasta:3.5.29" in build
        assert "--build-arg FOO=1" in build
        assert "The instance is stopped" in out

    def test_no_rebuild_only_records(self, tmp_path):
        rc, out, entry = _run(
            tmp_path, SET_PLAYBOOK,
            {"settings": ["FOO=2"], "no_rebuild": True},
            record={"buildArgs": ["FOO=1"], "buildBase": "base:1"})
        assert rc == 0, out
        assert entry["buildArgs"] == ["FOO=2"]
        assert entry["buildBase"] == "base:1"
        assert _builds(tmp_path) == []
        assert "next `canasta upgrade` rebuilds it" in out

    def test_a_failed_build_records_nothing(self, tmp_path):
        rc, out, entry = _run(
            tmp_path, SET_PLAYBOOK, {"settings": ["FOO=2"]},
            record={"buildArgs": ["FOO=1"]}, build_rc="1")
        assert rc != 0
        assert entry["buildArgs"] == ["FOO=1"]

    def test_unchanged_args_do_nothing(self, tmp_path):
        rc, out, entry = _run(
            tmp_path, SET_PLAYBOOK, {"settings": ["FOO=1"]},
            record={"buildArgs": ["FOO=1"]})
        assert rc == 0, out
        assert "are unchanged" in out
        assert _builds(tmp_path) == []

    def test_an_unchanged_image_is_not_restarted(self, tmp_path):
        rc, out, entry = _run(
            tmp_path, SET_PLAYBOOK, {"settings": ["FOO=2"]},
            record={"buildArgs": ["FOO=1"]},
            containers="c1", running=NEW, built=NEW)
        assert rc == 0, out
        assert entry["buildArgs"] == ["FOO=2"]
        assert "so it was not restarted" in out

    @pytest.mark.parametrize("bad", ["FOO", "=x", "1FOO=x", "FO-O=x"])
    def test_rejects_a_malformed_arg(self, tmp_path, bad):
        rc, out, entry = _run(
            tmp_path, SET_PLAYBOOK, {"settings": [bad]},
            record={"buildArgs": ["FOO=1"]})
        assert rc != 0
        assert "Invalid build argument" in out
        assert entry["buildArgs"] == ["FOO=1"]

    def test_rejects_the_output_tag_as_base(self, tmp_path):
        rc, out, entry = _run(
            tmp_path, SET_PLAYBOOK,
            {"settings": ["BASE_IMAGE=canasta:local"], "no_rebuild": True})
        assert rc != 0
        assert "collides with the build-from output tag" in out
        assert "buildArgs" not in entry

    def test_refuses_an_instance_not_built_from_source(self, tmp_path):
        rc, out, entry = _run(
            tmp_path, SET_PLAYBOOK, {"settings": ["FOO=1"]},
            record={"buildFrom": ""})
        assert rc != 0
        assert "is not built from source" in out


class TestUnset:
    def test_removes_and_rebuilds(self, tmp_path):
        rc, out, entry = _run(
            tmp_path, UNSET_PLAYBOOK, {"keys": ["FOO"]},
            record={"buildArgs": ["FOO=1", "BAR=2"]}, built=NEW)
        assert rc == 0, out
        assert entry["buildArgs"] == ["BAR=2"]
        (build,) = _builds(tmp_path)
        assert "FOO=1" not in build and "--build-arg BAR=2" in build

    def test_removing_the_last_arg_drops_the_field(self, tmp_path):
        rc, out, entry = _run(
            tmp_path, UNSET_PLAYBOOK, {"keys": ["FOO"], "no_rebuild": True},
            record={"buildArgs": ["FOO=1"]})
        assert rc == 0, out
        assert "buildArgs" not in entry

    def test_reports_a_key_that_is_not_recorded(self, tmp_path):
        rc, out, entry = _run(
            tmp_path, UNSET_PLAYBOOK, {"keys": ["NOPE"]},
            record={"buildArgs": ["FOO=1"]})
        assert rc == 0, out
        assert "Build argument 'NOPE' is not recorded for 'site'." in out
        assert "are unchanged" in out
        assert entry["buildArgs"] == ["FOO=1"]


def test_the_change_is_recorded_on_the_controller():
    with open(APPLY) as f:
        tasks = yaml.safe_load(f)
    record = next(t for t in tasks if "canasta_registry" in t)
    assert record["delegate_to"] == "canasta_controller"
    assert record["vars"] == {
        "ansible_connection": "local",
        "ansible_python_interpreter": "{{ ansible_playbook_python }}",
    }
    names = [t["name"] for t in tasks]
    assert names.index("Rebuild the image from source") < names.index(
        record["name"])
