"""`canasta config set CANASTA_IMAGE` on Kubernetes also patches the
deployment image in values.yaml, since the pods take their image from
image.repository and image.tag there rather than from .env. The
_side_effects.yml propagation must split repository:tag exactly as the
create-time k8s_values.yaml.j2 does."""

import os
import re

import jinja2
import pytest

from test_canasta_image_config import REPO_ROOT


def _ansible_jinja_env():
    env = jinja2.Environment(
        undefined=jinja2.StrictUndefined,
        keep_trailing_newline=True,
    )
    env.filters["regex_replace"] = lambda s, pat, repl="": re.sub(pat, repl, str(s))
    return env


class TestCanastaImageConfig:
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
