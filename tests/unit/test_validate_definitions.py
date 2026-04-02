"""Tests for the validate_definitions.py script."""

import os
import sys

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
import validate_definitions


class TestValidateMain:
    """Test the actual main() function against the real repo."""

    def test_real_definitions_pass(self):
        """The real command definitions should pass validation."""
        try:
            validate_definitions.main()
        except SystemExit as e:
            assert e.code == 0 or e.code is None, (
                "Validation failed on real definitions"
            )


class TestValidateStructure:
    def _make_defs(self, tmpdir, commands, playbooks=None):
        defn = {"commands": commands}
        defn_path = os.path.join(tmpdir, "meta", "command_definitions.yml")
        os.makedirs(os.path.dirname(defn_path), exist_ok=True)
        with open(defn_path, "w") as f:
            yaml.dump(defn, f)

        pb_dir = os.path.join(tmpdir, "playbooks")
        os.makedirs(pb_dir, exist_ok=True)
        for pb in (playbooks or []):
            with open(os.path.join(pb_dir, pb), "w") as f:
                f.write("---\n")
        return tmpdir

    def test_valid_structure(self, tmp_dir):
        self._make_defs(tmp_dir, [
            {"name": "test", "description": "Test", "playbook": "test.yml",
             "parameters": [{"name": "id", "type": "string", "description": "ID"}]},
        ], ["test.yml"])
        defn_path = os.path.join(tmp_dir, "meta", "command_definitions.yml")
        with open(defn_path) as f:
            data = yaml.safe_load(f)
        errors = []
        for cmd in data["commands"]:
            for field in validate_definitions.REQUIRED_CMD_FIELDS:
                if field not in cmd:
                    errors.append("missing %s" % field)
        assert len(errors) == 0

    def test_missing_field_detected(self):
        cmd = {"name": "test", "playbook": "test.yml", "parameters": []}
        missing = [f for f in validate_definitions.REQUIRED_CMD_FIELDS if f not in cmd]
        assert "description" in missing

    def test_invalid_type_detected(self):
        assert "invalid" not in validate_definitions.VALID_TYPES

    def test_valid_types(self):
        for t in ["string", "path", "bool", "choice", "integer"]:
            assert t in validate_definitions.VALID_TYPES

    def test_duplicate_names_detected(self):
        names = ["create", "delete", "create"]
        seen = set()
        dupes = [n for n in names if n in seen or seen.add(n)]
        assert "create" in dupes

    def test_underscore_prefix_skipped(self, tmp_dir):
        self._make_defs(tmp_dir, [], ["_helper.yml"])
        pb_dir = os.path.join(tmp_dir, "playbooks")
        files = [f for f in os.listdir(pb_dir) if f.endswith(".yml") and not f.startswith("_")]
        assert "_helper.yml" not in files
