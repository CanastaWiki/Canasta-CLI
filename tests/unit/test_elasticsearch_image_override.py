"""CANASTA_ELASTICSEARCH_IMAGE stays a known key so `config set` refuses it as
unsupported rather than as an unknown key, and an upgrade can remove it."""

import os

import yaml


REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")


def _read(*parts):
    with open(os.path.join(REPO_ROOT, *parts)) as f:
        return f.read()


class TestConfigKeyRegistration:

    def test_key_is_known(self):
        data = yaml.safe_load(_read("roles", "config", "defaults", "main.yml"))
        keys = {k["name"]: k for k in data["canasta_known_keys"]}
        assert "CANASTA_ELASTICSEARCH_IMAGE" in keys
        assert keys["CANASTA_ELASTICSEARCH_IMAGE"]["group"] == "Docker Image"
