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


class TestDirectOnlyInvariants:
    """Invariants that have to hold for 'direct_only: true' commands.

    These catch two failure modes that validate_definitions.py can't
    see on its own:

    1. A command is declared 'direct_only: true' in
       command_definitions.yml but has no matching handler registered
       in direct_commands.py. At runtime the command would have no
       code path at all — no handler, no playbook.
    2. A command has 'direct_only: true' AND a 'playbook:' field (the
       XOR already caught by validate_definitions, re-asserted here
       at the data layer so it's discoverable in this test file too).
    """

    def _real_commands(self):
        script_dir = os.path.dirname(
            os.path.abspath(validate_definitions.__file__)
        )
        defn_path = os.path.join(
            os.path.dirname(script_dir),
            "meta", "command_definitions.yml",
        )
        with open(defn_path) as f:
            return yaml.safe_load(f).get("commands", [])

    def _direct_commands_module(self):
        # direct_commands imports yaml at module scope; add repo root
        # to sys.path so the import resolves in CI environments that
        # don't ship the repo as a package.
        script_dir = os.path.dirname(
            os.path.abspath(validate_definitions.__file__)
        )
        repo_root = os.path.dirname(script_dir)
        if repo_root not in sys.path:
            sys.path.insert(0, repo_root)
        import direct_commands
        return direct_commands

    def test_every_direct_only_command_has_a_handler(self):
        dc = self._direct_commands_module()
        missing = []
        for cmd in self._real_commands():
            if cmd.get("direct_only"):
                name = cmd.get("name", "?")
                if not dc.is_direct_command(name):
                    missing.append(name)
        assert not missing, (
            "direct_only commands with no direct_commands.py handler: %s"
            % ", ".join(missing)
        )

    def test_no_command_declares_both_direct_only_and_playbook(self):
        conflicts = []
        for cmd in self._real_commands():
            if cmd.get("direct_only") and cmd.get("playbook"):
                conflicts.append(cmd.get("name", "?"))
        assert not conflicts, (
            "Commands with both direct_only AND playbook: %s"
            % ", ".join(conflicts)
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


class TestDescriptionLint:
    """Guard against redundant parentheticals in parameter descriptions
    that duplicate what the wiki flag table already conveys:

    - '(optional)' is redundant with an unchecked Required column.
    - '(required)' is redundant with a checked Required column.
    - '(default: X)' is redundant when the parameter has a literal
      `default: X` field (the wiki table's Default column shows it).
    """

    REPO_ROOT = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..")
    )
    DEFS_PATH = os.path.join(REPO_ROOT, "meta", "command_definitions.yml")

    def _all_params(self):
        with open(self.DEFS_PATH) as f:
            data = yaml.safe_load(f)
        params = [("<global>", p) for p in data.get("global_flags", [])]
        for c in data.get("commands", []):
            for p in c.get("parameters", []) or []:
                params.append((c["name"], p))
        return params

    def test_no_optional_markers(self):
        offenders = []
        for cmd, p in self._all_params():
            if "(optional)" in p.get("description", "").lower():
                offenders.append("%s.%s" % (cmd, p["name"]))
        assert not offenders, (
            "descriptions contain redundant '(optional)' — remove it "
            "(the Required column already conveys this):\n  "
            + "\n  ".join(offenders)
        )

    def test_no_required_markers(self):
        offenders = []
        for cmd, p in self._all_params():
            if "(required)" in p.get("description", "").lower():
                offenders.append("%s.%s" % (cmd, p["name"]))
        assert not offenders, (
            "descriptions contain redundant '(required)' — remove it "
            "(the Required column already conveys this):\n  "
            + "\n  ".join(offenders)
        )

    def test_no_redundant_default_markers(self):
        import re
        pat = re.compile(r"\(default:\s*[^)]+\)", re.IGNORECASE)
        offenders = []
        for cmd, p in self._all_params():
            # Only flag when the YAML also has a literal default — a
            # parenthetical 'default: localhost' describing runtime
            # behavior on a param with no YAML default is meaningful.
            if "default" in p and pat.search(p.get("description", "")):
                offenders.append("%s.%s" % (cmd, p["name"]))
        assert not offenders, (
            "descriptions contain '(default: ...)' that duplicates "
            "the YAML `default:` field — remove from the description:\n  "
            + "\n  ".join(offenders)
        )
