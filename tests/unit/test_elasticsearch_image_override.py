"""Tests for overriding the Elasticsearch image via CANASTA_ELASTICSEARCH_IMAGE.

This mirrors the existing CANASTA_IMAGE mechanism: on Compose the image is
read from .env (`${CANASTA_ELASTICSEARCH_IMAGE:-<default>}`); on Kubernetes
`config set` deep-merges `elasticsearch.image` into values.yaml via
_side_effects.yml.

The key property is composition with the enable/disable profile: setting the
image must not disturb `elasticsearch.enabled` (CANASTA_ENABLE_ELASTICSEARCH),
and toggling enablement must not drop a previously-set image. On K8s that is
guaranteed by `combine(recursive=True)`; these tests model that merge.
"""

import os
import re

import jinja2
import yaml


REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")


def _read(*parts):
    with open(os.path.join(REPO_ROOT, *parts)) as f:
        return f.read()


def _render_override(expr, **vars):
    """Render an Ansible set_fact dict expression the way the playbook does."""
    env = jinja2.Environment(undefined=jinja2.StrictUndefined)
    return yaml.safe_load(env.from_string("{{ %s }}" % expr).render(**vars))


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

    def test_key_is_in_k8s_values_propagation_allowlist(self):
        side_effects = _read("roles", "config", "tasks", "_side_effects.yml")
        assert "'CANASTA_ELASTICSEARCH_IMAGE'" in side_effects
        # The override block must target elasticsearch.image specifically.
        assert "{'elasticsearch': {'image': _config_value}}" in side_effects


class TestComposeWiring:

    def test_image_is_env_overridable_with_default_preserved(self):
        compose = _read("roles", "orchestrator", "files", "compose", "docker-compose.yml")
        assert "${CANASTA_ELASTICSEARCH_IMAGE:-" in compose
        # Default kept, so an unset var leaves stock Elasticsearch — at the
        # same version the Kubernetes chart pins (parity guard: the two
        # orchestrators must not drift apart again).
        values = _read("roles", "orchestrator", "files", "helm", "canasta",
                       "values.yaml")
        match = re.search(r"image:\s*elasticsearch:([\w.-]+)", values)
        assert match, "k8s chart no longer pins an elasticsearch image?"
        assert f"elasticsearch:{match.group(1)}" in compose
        # And the pinned line must be the one CirrusSearch supports — its
        # README (REL1_43) says "Only Elasticsearch v7.10 is supported".
        # Update this alongside a CirrusSearch upgrade that widens
        # support, not before.
        assert match.group(1).startswith("7.10."), (
            "stock Elasticsearch moved off the 7.10.x line CirrusSearch "
            "supports"
        )


class TestKubernetesPropagation:

    def test_override_targets_elasticsearch_image(self):
        out = _render_override(
            "{'elasticsearch': {'image': _config_value}}",
            _config_value="myreg/es-icu:7.17.9",
        )
        assert out == {"elasticsearch": {"image": "myreg/es-icu:7.17.9"}}

    def test_setting_image_preserves_enabled(self):
        # Profile already on; set a custom image -> both survive.
        values = {"elasticsearch": {"enabled": True}}
        override = _render_override(
            "{'elasticsearch': {'image': _config_value}}",
            _config_value="myreg/es-icu:7.17.9",
        )
        merged = _combine_recursive(values, override)
        assert merged["elasticsearch"] == {
            "enabled": True,
            "image": "myreg/es-icu:7.17.9",
        }

    def test_toggling_enabled_preserves_image(self):
        # Custom image already set; flip the profile on -> image survives.
        values = {"elasticsearch": {"image": "myreg/es-icu:7.17.9"}}
        override = _render_override(
            "{'elasticsearch': {'enabled': _config_value | lower == 'true'}}",
            _config_value="true",
        )
        merged = _combine_recursive(values, override)
        assert merged["elasticsearch"] == {
            "image": "myreg/es-icu:7.17.9",
            "enabled": True,
        }

    def test_disabling_preserves_image_for_later_reenable(self):
        values = {"elasticsearch": {"enabled": True, "image": "myreg/es-icu:7.17.9"}}
        override = _render_override(
            "{'elasticsearch': {'enabled': _config_value | lower == 'true'}}",
            _config_value="false",
        )
        merged = _combine_recursive(values, override)
        assert merged["elasticsearch"]["enabled"] is False
        assert merged["elasticsearch"]["image"] == "myreg/es-icu:7.17.9"
