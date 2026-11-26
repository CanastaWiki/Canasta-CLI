"""`config set CANASTA_ELASTICSEARCH_IMAGE` on Kubernetes deep-merges
`elasticsearch.image` into values.yaml through _side_effects.yml. The merge
models Ansible's `combine(recursive=True)`, so setting the image keeps
`elasticsearch.enabled` and toggling enablement keeps a set image."""

import jinja2
import yaml

from test_elasticsearch_image_override import _combine_recursive, _read


def _render_override(expr, **vars):
    """Render an Ansible set_fact dict expression the way the playbook does."""
    env = jinja2.Environment(undefined=jinja2.StrictUndefined)
    return yaml.safe_load(env.from_string("{{ %s }}" % expr).render(**vars))


class TestConfigKeyRegistration:
    def test_key_is_in_k8s_values_propagation_allowlist(self):
        side_effects = _read("roles", "config", "tasks", "_side_effects.yml")
        assert "'CANASTA_ELASTICSEARCH_IMAGE'" in side_effects
        # The override block must target elasticsearch.image specifically.
        assert "{'elasticsearch': {'image': _config_value}}" in side_effects


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
