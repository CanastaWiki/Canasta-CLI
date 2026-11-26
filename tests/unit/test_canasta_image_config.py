"""`CANASTA_IMAGE` is a config key `canasta config set` accepts; the web
service image is `${CANASTA_IMAGE}` read from .env."""

import os

import yaml


REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")


class TestCanastaImageConfig:

    def test_canasta_image_is_a_known_config_key(self):
        path = os.path.join(
            REPO_ROOT, "roles", "config", "defaults", "main.yml",
        )
        with open(path) as f:
            defaults = yaml.safe_load(f)
        names = [e["name"] for e in defaults["canasta_known_keys"]]
        assert "CANASTA_IMAGE" in names
