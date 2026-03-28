"""Tests for the generate_docs.py script."""

import os
import sys
import tempfile

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
import generate_docs


SAMPLE_DEFINITIONS = {
    "global_flags": [
        {"name": "verbose", "short": "v", "type": "bool", "default": False, "description": "Enable verbose output"},
    ],
    "commands": [
        {
            "name": "create",
            "description": "Create a new instance",
            "long_description": "Create a new Canasta instance with all the bells and whistles.",
            "examples": ["canasta create -i mysite -w main"],
            "playbook": "create.yml",
            "parameters": [
                {"name": "id", "short": "i", "type": "string", "required": True, "description": "Instance ID"},
                {"name": "wiki", "short": "w", "type": "string", "required_unless": "yamlfile", "description": "Wiki ID"},
                {"name": "orchestrator", "short": "o", "type": "choice", "choices": ["compose", "kubernetes"], "default": "compose", "description": "Orchestrator"},
                {"name": "password", "type": "string", "sensitive": True, "description": "Admin password"},
            ],
        },
        {
            "name": "version",
            "description": "Display version",
            "playbook": "version.yml",
            "parameters": [],
        },
    ],
}


class TestFormatFlagName:
    def test_long_only(self):
        assert generate_docs.format_flag_name({"name": "keep_config"}) == "--keep-config"

    def test_short_and_long(self):
        result = generate_docs.format_flag_name({"name": "id", "short": "i"})
        assert "-i" in result
        assert "--id" in result

    def test_underscore_to_hyphen(self):
        result = generate_docs.format_flag_name({"name": "keep_config"})
        assert "--keep-config" in result


class TestFormatTypeDefault:
    def test_bool(self):
        assert generate_docs.format_type_default({"type": "bool"}) == "flag"

    def test_choice(self):
        result = generate_docs.format_type_default({"type": "choice", "choices": ["a", "b"]})
        assert "a" in result
        assert "b" in result

    def test_with_default(self):
        result = generate_docs.format_type_default({"type": "string", "default": "foo"})
        assert "foo" in result

    def test_no_default(self):
        result = generate_docs.format_type_default({"type": "string"})
        assert result == "string"


class TestCommandToMarkdown:
    def test_contains_title(self):
        md = generate_docs.command_to_markdown(SAMPLE_DEFINITIONS["commands"][0])
        assert "# canasta create" in md

    def test_contains_description(self):
        md = generate_docs.command_to_markdown(SAMPLE_DEFINITIONS["commands"][0])
        assert "Create a new instance" in md

    def test_contains_flags_table(self):
        md = generate_docs.command_to_markdown(SAMPLE_DEFINITIONS["commands"][0])
        assert "| Flag |" in md
        assert "--id" in md

    def test_contains_examples(self):
        md = generate_docs.command_to_markdown(SAMPLE_DEFINITIONS["commands"][0])
        assert "canasta create -i mysite" in md

    def test_sensitive_note(self):
        md = generate_docs.command_to_markdown(SAMPLE_DEFINITIONS["commands"][0])
        assert "auto-generated" in md

    def test_required_flag(self):
        md = generate_docs.command_to_markdown(SAMPLE_DEFINITIONS["commands"][0])
        assert "Yes" in md

    def test_required_unless(self):
        md = generate_docs.command_to_markdown(SAMPLE_DEFINITIONS["commands"][0])
        assert "Unless" in md


class TestCommandToText:
    def test_contains_title(self):
        text = generate_docs.command_to_text(SAMPLE_DEFINITIONS["commands"][0])
        assert "canasta create" in text

    def test_contains_flags(self):
        text = generate_docs.command_to_text(SAMPLE_DEFINITIONS["commands"][0])
        assert "--id" in text
        assert "(required)" in text


class TestGenerateIndex:
    def test_contains_commands(self):
        index = generate_docs.generate_index(SAMPLE_DEFINITIONS["commands"])
        assert "create" in index
        assert "version" in index

    def test_markdown_table(self):
        index = generate_docs.generate_index(SAMPLE_DEFINITIONS["commands"])
        assert "| Command |" in index


class TestGenerateToDirectory:
    def test_generates_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            defn_path = os.path.join(tmpdir, "defs.yml")
            with open(defn_path, "w") as f:
                yaml.dump(SAMPLE_DEFINITIONS, f)
            out_dir = os.path.join(tmpdir, "docs")
            os.makedirs(out_dir)

            data = generate_docs.load_definitions(defn_path)
            for cmd in data["commands"]:
                md = generate_docs.command_to_markdown(cmd, data.get("global_flags"))
                with open(os.path.join(out_dir, "%s.md" % cmd["name"]), "w") as f:
                    f.write(md)

            assert os.path.exists(os.path.join(out_dir, "create.md"))
            assert os.path.exists(os.path.join(out_dir, "version.md"))
