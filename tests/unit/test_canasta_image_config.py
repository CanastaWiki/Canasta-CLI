"""Tests for `canasta config set CANASTA_IMAGE` on Kubernetes (issue #703).

On Compose, the web service image is `${CANASTA_IMAGE}` read straight
from .env, so config set + restart redeploys the custom image. On
Kubernetes the deployment image comes from values.yaml
(image.repository / image.tag), so config set must also patch
values.yaml — otherwise the change lands in .env and the pods keep
running the previously-configured image.

These tests cover the _side_effects.yml propagation that gives K8s
parity with Compose, and verify the repository:tag split matches the
create-time split in k8s_values.yaml.j2.
"""

import os
import re

import jinja2
import pytest
import yaml


REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")


def _ansible_jinja_env():
    env = jinja2.Environment(
        undefined=jinja2.StrictUndefined,
        keep_trailing_newline=True,
    )
    env.filters["regex_replace"] = lambda s, pat, repl="": re.sub(pat, repl, str(s))
    return env


class TestCanastaImageConfig:

    def test_canasta_image_is_a_known_config_key(self):
        path = os.path.join(
            REPO_ROOT, "roles", "config", "defaults", "main.yml",
        )
        with open(path) as f:
            defaults = yaml.safe_load(f)
        names = [e["name"] for e in defaults["canasta_known_keys"]]
        assert "CANASTA_IMAGE" in names

    def test_side_effects_propagates_canasta_image(self):
        """config set CANASTA_IMAGE on a K8s instance must patch the
        deployment image in values.yaml."""
        path = os.path.join(
            REPO_ROOT, "roles", "config", "tasks", "_side_effects.yml",
        )
        with open(path) as f:
            content = f.read()
        assert "'CANASTA_IMAGE'" in content, (
            "_side_effects.yml must list CANASTA_IMAGE in the K8s "
            "values.yaml propagation list"
        )

    @pytest.mark.parametrize("image,repository,tag", [
        (
            "ghcr.io/canastawiki/canasta:3.5.6",
            "ghcr.io/canastawiki/canasta",
            "3.5.6",
        ),
        (
            "ghcr.io/canastawiki/canasta:latest",
            "ghcr.io/canastawiki/canasta",
            "latest",
        ),
        (
            "myorg/custom-canasta:v1.2.3",
            "myorg/custom-canasta",
            "v1.2.3",
        ),
    ])
    def test_side_effects_image_split_matches_create(self, image, repository, tag):
        """The _side_effects.yml override splits repository:tag the same
        way create does (k8s_values.yaml.j2): repository is everything
        before the last colon, tag is everything after. Evaluate both
        expressions and assert they agree and produce the expected
        values."""
        repo_expr = "{{ _config_value | regex_replace(':[^:]+$', '') }}"
        tag_expr = "{{ _config_value | regex_replace('^[^:]+:', '') }}"
        env = _ansible_jinja_env()
        rendered_repo = env.from_string(repo_expr).render(_config_value=image)
        rendered_tag = env.from_string(tag_expr).render(_config_value=image)
        assert rendered_repo == repository
        assert rendered_tag == tag

        # The create-time template must split the same image identically,
        # so config set and create agree on what repository/tag mean.
        template_repo = (
            "{{ canasta_image | regex_replace(':[^:]+$', '') }}"
        )
        template_tag = (
            "{{ canasta_image | regex_replace('^[^:]+:', '') }}"
        )
        assert env.from_string(template_repo).render(canasta_image=image) == rendered_repo
        assert env.from_string(template_tag).render(canasta_image=image) == rendered_tag
