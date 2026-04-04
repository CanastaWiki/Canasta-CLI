"""Shared fixtures for Canasta-Ansible unit tests."""

import json
import os
import sys
import tempfile

import pytest

# Add the module library paths so we can import them directly
ROLES_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "roles")
sys.path.insert(0, os.path.join(ROLES_DIR, "common", "library"))
sys.path.insert(0, os.path.join(ROLES_DIR, "extensions_skins", "library"))


@pytest.fixture
def tmp_dir():
    """Create a temporary directory that is cleaned up after the test."""
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture
def sample_config(tmp_dir):
    """Create a sample conf.json with two instances."""
    data = {
        "Instances": {
            "mysite": {
                "id": "mysite",
                "path": os.path.join(tmp_dir, "mysite"),
                "orchestrator": "compose",
            },
            "devsite": {
                "id": "devsite",
                "path": os.path.join(tmp_dir, "devsite"),
                "orchestrator": "compose",
                "devMode": True,
            },
        }
    }
    # Create instance directories
    os.makedirs(os.path.join(tmp_dir, "mysite"), exist_ok=True)
    os.makedirs(os.path.join(tmp_dir, "devsite"), exist_ok=True)
    # Write conf.json
    conf_path = os.path.join(tmp_dir, "conf.json")
    with open(conf_path, "w") as f:
        json.dump(data, f, indent=4)
    return tmp_dir, data


@pytest.fixture
def sample_env_file(tmp_dir):
    """Create a sample .env file."""
    env_content = (
        '# Canasta environment\n'
        'MW_SITE_SERVER=https://example.com\n'
        'MW_SITE_NAME="My Wiki"\n'
        'MYSQL_ROOT_PASSWORD=secret123\n'
        'CANASTA_WIKI_DOMAIN=example.com\n'
        'EMPTY_VALUE=\n'
        '# A comment\n'
        'QUOTED_VALUE="hello world"\n'
        'EQUALS_IN_VALUE=key=val\n'
    )
    env_path = os.path.join(tmp_dir, ".env")
    with open(env_path, "w") as f:
        f.write(env_content)
    return env_path


@pytest.fixture
def sample_wikis_yaml(tmp_dir):
    """Create a sample wikis.yaml."""
    content = (
        "wikis:\n"
        "  - id: main\n"
        "    url: example.com\n"
        '    name: "Main Wiki"\n'
        "  - id: docs\n"
        "    url: example.com/docs\n"
        '    name: "Documentation"\n'
    )
    config_dir = os.path.join(tmp_dir, "config")
    os.makedirs(config_dir, exist_ok=True)
    yaml_path = os.path.join(config_dir, "wikis.yaml")
    with open(yaml_path, "w") as f:
        f.write(content)
    return tmp_dir


@pytest.fixture
def sample_settings_yaml(tmp_dir):
    """Create a sample settings.yaml for extensions/skins."""
    content = (
        "# Canasta will add and remove lines from this file as extensions and skins are enabled and disabled.\n"
        "extensions:\n"
        "  - Cite\n"
        "  - VisualEditor\n"
        "skins:\n"
        "  - Timeless\n"
        "  - Vector\n"
    )
    settings_dir = os.path.join(tmp_dir, "config", "settings", "global")
    os.makedirs(settings_dir, exist_ok=True)
    yaml_path = os.path.join(settings_dir, "settings.yaml")
    with open(yaml_path, "w") as f:
        f.write(content)
    return tmp_dir
