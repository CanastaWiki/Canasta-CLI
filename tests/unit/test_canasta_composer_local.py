"""Tests for the canasta_composer_local Ansible module."""

import json
import os

import canasta_composer_local
from mock_ansible import run_module_with_params


def _run(tmp_dir, include, state="present"):
    return run_module_with_params(
        canasta_composer_local,
        {"instance_path": tmp_dir, "include": include, "state": state})


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


class TestAbsentState:
    def test_removes_registered_entries(self, tmp_dir):
        _run(tmp_dir, ["extensions/Foo/composer.json",
                       "skins/Bar/composer.json"])
        result, failed, _ = _run(tmp_dir, ["extensions/Foo/composer.json"],
                                 state="absent")
        assert failed is False
        assert result["changed"] is True
        assert result["removed"] == ["extensions/Foo/composer.json"]
        assert result["include"] == ["skins/Bar/composer.json"]

    def test_idempotent_when_already_absent(self, tmp_dir):
        _run(tmp_dir, ["extensions/Foo/composer.json"])
        result, failed, _ = _run(tmp_dir, ["extensions/Foo/composer.json"],
                                 state="absent")
        assert failed is False
        assert result["changed"] is True
        again, failed, _ = _run(tmp_dir, ["extensions/Foo/composer.json"],
                                state="absent")
        assert failed is False
        assert again["changed"] is False
        assert again["removed"] == []

    def test_noop_without_file(self, tmp_dir):
        result, failed, _ = _run(tmp_dir, ["extensions/Foo/composer.json"],
                                 state="absent")
        assert failed is False
        assert result["changed"] is False
        assert not os.path.exists(_path(tmp_dir))

    def test_preserves_other_entries(self, tmp_dir):
        _run(tmp_dir, ["extensions/Foo/composer.json",
                       "extensions/Bar/composer.json",
                       "skins/Baz/composer.json"])
        _, failed, _ = _run(tmp_dir, ["extensions/Foo/composer.json"],
                            state="absent")
        assert failed is False
        data = _read(tmp_dir)
        assert data["extra"]["merge-plugin"]["include"] == [
            "extensions/Bar/composer.json", "skins/Baz/composer.json"]

    def test_fails_on_non_list_include(self, tmp_dir):
        os.mkdir(os.path.join(tmp_dir, "config"))
        with open(_path(tmp_dir), "w") as f:
            json.dump({"extra": {"merge-plugin": {"include": "nope"}}}, f)
        _, failed, msg = _run(tmp_dir, ["extensions/Foo/composer.json"],
                              state="absent")
        assert failed is True
        assert "not a list" in msg


class TestComposerLocalFormatting:
    # As the image writes it: PHP JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES.
    IMAGE_FILE = (
        '{\n'
        '    "extra": {\n'
        '        "merge-plugin": {\n'
        '            "include": [\n'
        '                "extensions/SemanticMediaWiki/composer.json"\n'
        '            ]\n'
        '        }\n'
        '    }\n'
        '}\n'
    )

    def _seed(self, tmp_dir, text):
        os.mkdir(os.path.join(tmp_dir, "config"))
        with open(_path(tmp_dir), "w") as f:
            f.write(text)

    def test_adding_an_include_changes_only_the_include_lines(self, tmp_dir):
        self._seed(tmp_dir, self.IMAGE_FILE)
        _run(tmp_dir, ["extensions/Foo/composer.json"])
        with open(_path(tmp_dir)) as f:
            written = f.read()
        assert written == self.IMAGE_FILE.replace(
            '"extensions/SemanticMediaWiki/composer.json"\n',
            '"extensions/SemanticMediaWiki/composer.json",\n'
            '                "extensions/Foo/composer.json"\n')

    def test_keeps_a_two_space_file_at_two_spaces(self, tmp_dir):
        self._seed(tmp_dir, json.dumps(
            {"extra": {"merge-plugin": {"include": []}}}, indent=2) + "\n")
        _run(tmp_dir, ["extensions/Foo/composer.json"])
        with open(_path(tmp_dir)) as f:
            assert f.read().splitlines()[1] == '  "extra": {'

    def test_new_file_matches_the_image_format(self, tmp_dir):
        _run(tmp_dir, ["extensions/SemanticMediaWiki/composer.json"])
        with open(_path(tmp_dir)) as f:
            assert f.read() == self.IMAGE_FILE
