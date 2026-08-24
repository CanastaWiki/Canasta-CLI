"""Tests for the canasta_composer_local Ansible module."""

import json
import os

import canasta_composer_local
from mock_ansible import run_module_with_params


def _run(tmp_dir, include):
    return run_module_with_params(
        canasta_composer_local,
        {"instance_path": tmp_dir, "include": include})


def _path(tmp_dir):
    return os.path.join(tmp_dir, "config", "composer.local.json")


def _read(tmp_dir):
    with open(_path(tmp_dir)) as handle:
        return json.load(handle)


class TestComposerLocal:
    def test_creates_file_when_missing(self, tmp_dir):
        result, failed, _ = _run(tmp_dir, ["extensions/Foo/composer.json"])
        assert failed is False
        assert result["changed"] is True
        data = _read(tmp_dir)
        assert data["extra"]["merge-plugin"]["include"] == [
            "extensions/Foo/composer.json"]

    def test_merges_into_existing_includes(self, tmp_dir):
        os.mkdir(os.path.join(tmp_dir, "config"))
        with open(_path(tmp_dir), "w") as f:
            json.dump({"extra": {"merge-plugin": {"include": [
                "skins/Bar/composer.json"]}}}, f)
        result, failed, _ = _run(tmp_dir, [
            "extensions/Foo/composer.json", "skins/Bar/composer.json"])
        assert failed is False
        assert result["changed"] is True
        assert result["include"] == [
            "skins/Bar/composer.json", "extensions/Foo/composer.json"]

    def test_preserves_unrelated_keys(self, tmp_dir):
        os.mkdir(os.path.join(tmp_dir, "config"))
        with open(_path(tmp_dir), "w") as f:
            json.dump({"require": {"wikimedia/foo": "^1.0"}}, f)
        _run(tmp_dir, ["extensions/Foo/composer.json"])
        data = _read(tmp_dir)
        assert data["require"] == {"wikimedia/foo": "^1.0"}

    def test_idempotent(self, tmp_dir):
        _run(tmp_dir, ["extensions/Foo/composer.json"])
        result, failed, _ = _run(tmp_dir, ["extensions/Foo/composer.json"])
        assert failed is False
        assert result["changed"] is False

    def test_fails_on_malformed_json(self, tmp_dir):
        os.mkdir(os.path.join(tmp_dir, "config"))
        with open(_path(tmp_dir), "w") as f:
            f.write("{not json")
        _, failed, msg = _run(tmp_dir, ["extensions/Foo/composer.json"])
        assert failed is True
        assert "parse" in msg

    def test_fails_on_non_list_include(self, tmp_dir):
        os.mkdir(os.path.join(tmp_dir, "config"))
        with open(_path(tmp_dir), "w") as f:
            json.dump({"extra": {"merge-plugin": {"include": "nope"}}}, f)
        _, failed, msg = _run(tmp_dir, ["extensions/Foo/composer.json"])
        assert failed is True
        assert "not a list" in msg


class TestCheckMode:
    def test_no_write_in_check_mode(self, tmp_dir):
        result, failed, _ = run_module_with_params(
            canasta_composer_local,
            {"instance_path": tmp_dir,
             "include": ["extensions/X/composer.json"],
             "_check_mode": True})
        assert failed is False
        assert result["changed"] is True
        assert not os.path.exists(_path(tmp_dir))
