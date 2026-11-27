"""Tests for overriding the Elasticsearch image via CANASTA_ELASTICSEARCH_IMAGE.

Kubernetes only: `config set` deep-merges `elasticsearch.image` into
values.yaml via _side_effects.yml.

The key property is composition with the enable/disable profile: setting the
image must not disturb `elasticsearch.enabled` (CANASTA_ENABLE_ELASTICSEARCH),
and toggling enablement must not drop a previously-set image. On K8s that is
guaranteed by `combine(recursive=True)`; these tests model that merge.
"""

import os

import yaml


REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")


def _read(*parts):
    with open(os.path.join(REPO_ROOT, *parts)) as f:
        return f.read()


def _combine_recursive(base, override):
    """Mirror Ansible's `combine(recursive=True)`: deep-merge, override wins."""
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _combine_recursive(result[k], v)
        else:
            result[k] = v
    return result


class TestConfigKeyRegistration:

    def test_key_is_known(self):
        data = yaml.safe_load(_read("roles", "config", "defaults", "main.yml"))
        keys = {k["name"]: k for k in data["canasta_known_keys"]}
        assert "CANASTA_ELASTICSEARCH_IMAGE" in keys
        assert keys["CANASTA_ELASTICSEARCH_IMAGE"]["group"] == "Docker Image"
