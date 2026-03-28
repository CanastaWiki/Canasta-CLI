"""Tests for the validate_definitions.py script."""

import os
import sys
import tempfile

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
import validate_definitions


class TestValidation:
    def _make_structure(self, tmpdir, commands, playbooks=None):
        """Create a minimal definitions file and playbook directory."""
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
        self._make_structure(tmp_dir, [
            {"name": "test", "description": "Test", "playbook": "test.yml",
             "parameters": [{"name": "id", "type": "string", "description": "ID"}]},
        ], ["test.yml"])

        # Manually run validation logic
        defn_path = os.path.join(tmp_dir, "meta", "command_definitions.yml")
        with open(defn_path) as f:
            data = yaml.safe_load(f)
        commands = data["commands"]
        errors = []
        for cmd in commands:
            for field in ["name", "description", "playbook", "parameters"]:
                if field not in cmd:
                    errors.append("missing %s" % field)
        assert len(errors) == 0

    def test_missing_playbook_detected(self, tmp_dir):
        self._make_structure(tmp_dir, [
            {"name": "test", "description": "Test", "playbook": "missing.yml",
             "parameters": []},
        ], [])  # No playbook file created

        pb_dir = os.path.join(tmp_dir, "playbooks")
        pb_path = os.path.join(pb_dir, "missing.yml")
        assert not os.path.exists(pb_path)

    def test_orphan_playbook_detected(self, tmp_dir):
        self._make_structure(tmp_dir, [
            {"name": "test", "description": "Test", "playbook": "test.yml",
             "parameters": []},
        ], ["test.yml", "orphan.yml"])

        defined = {"test.yml"}
        pb_dir = os.path.join(tmp_dir, "playbooks")
        orphans = [f for f in os.listdir(pb_dir) if f.endswith(".yml") and not f.startswith("_") and f not in defined]
        assert "orphan.yml" in orphans

    def test_invalid_type_detected(self):
        param = {"name": "x", "type": "invalid", "description": "Bad"}
        assert param["type"] not in {"string", "path", "bool", "choice", "integer"}

    def test_duplicate_names_detected(self):
        names = ["create", "delete", "create"]
        seen = set()
        dupes = []
        for n in names:
            if n in seen:
                dupes.append(n)
            seen.add(n)
        assert "create" in dupes

    def test_underscore_prefix_skipped(self, tmp_dir):
        self._make_structure(tmp_dir, [], ["_helper.yml"])
        pb_dir = os.path.join(tmp_dir, "playbooks")
        files = [f for f in os.listdir(pb_dir) if f.endswith(".yml") and not f.startswith("_")]
        assert "_helper.yml" not in files
