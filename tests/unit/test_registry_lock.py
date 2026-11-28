"""conf.json is read through canasta_config and changed under its lock, so a
damaged file is an error rather than a traceback and two commands changing
it at once keep both changes."""

import contextlib
import json
import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import canasta_config
import canasta_registry
from direct_commands import _helpers, info
from mock_ansible import run_module_with_params


def test_an_empty_registry_reads_as_no_instances(tmp_path):
    conf = tmp_path / "conf.json"
    conf.write_text("")
    assert _helpers._read_registry(str(conf)) == {}


def test_a_truncated_registry_is_an_error_not_a_traceback(tmp_path, capsys):
    conf = tmp_path / "conf.json"
    conf.write_text('{"Instances": {"one": {"path": "/srv/o')
    with pytest.raises(SystemExit) as exc:
        _helpers._read_registry(str(conf))
    assert exc.value.code == 1
    assert "Error: cannot read the registry %s" % conf in capsys.readouterr().err


def test_concurrent_updates_keep_every_change(tmp_path):
    conf = tmp_path / "conf.json"
    conf.write_text(json.dumps({"Instances": {}}))

    def add(iid):
        def update(instances):
            time.sleep(0.05)
            instances[iid] = {"path": "/srv/" + iid}
        _helpers._update_registry(str(conf), update)

    threads = [threading.Thread(target=add, args=("i%d" % n,)) for n in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(_helpers._read_registry(str(conf))) == ["i%d" % n for n in range(6)]


def test_cleanup_keeps_an_instance_registered_while_it_probed(tmp_path, monkeypatch, capsys):
    conf = tmp_path / "conf.json"
    conf.write_text(json.dumps({"Instances": {
        "gone": {"path": str(tmp_path / "gone"), "host": "localhost"}}}))
    monkeypatch.setattr(_helpers, "_get_config_dir", lambda: str(tmp_path))

    def classify(iid, inst):
        _helpers._update_registry(
            str(conf), lambda current: current.update(new={"path": "/srv/new"}))
        return "missing"

    monkeypatch.setattr(info, "_classify_for_cleanup", classify)
    args = type("Args", (), {"cleanup": True, "host": None, "force": False,
                             "dry_run": False})()
    info.cmd_list(args)
    assert sorted(_helpers._read_registry(str(conf))) == ["new"]
    assert "Removed stale entries: gone" in capsys.readouterr().out


def test_the_registry_module_holds_the_lock(tmp_path, monkeypatch):
    held = []

    @contextlib.contextmanager
    def recording(config_dir):
        held.append(config_dir)
        yield

    monkeypatch.setattr(canasta_registry, "locked", recording)
    _, failed, _ = run_module_with_params(canasta_registry, {
        "state": "present", "id": "one", "path": str(tmp_path / "one"),
        "orchestrator": "compose", "dev_mode": False, "build_from": None,
        "host": None, "filter_host": None, "config_dir": str(tmp_path),
    })
    assert not failed
    assert held == [str(tmp_path)]


def test_the_lock_excludes_a_second_holder(tmp_path):
    order = []

    def second():
        with canasta_config.locked(str(tmp_path)):
            order.append("second")

    with canasta_config.locked(str(tmp_path)):
        t = threading.Thread(target=second)
        t.start()
        time.sleep(0.2)
        order.append("first")
    t.join()
    assert order == ["first", "second"]
