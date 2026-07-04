"""Tests for the image_ref validator used by `canasta image push --name`.

The value is a Docker repository name with an optional tag, per the
distribution spec, and deliberately without a registry host — image push
prepends the in-cluster registry's address itself.
"""

import os
import sys

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, REPO_ROOT)

import canasta  # noqa: E402


REGEX = canasta._VALIDATORS["image_ref"][0]


class TestImageRefAccepts:
    def test_plain_name(self):
        assert REGEX.match("myapp-elasticsearch")

    def test_name_with_tag(self):
        assert REGEX.match("custom-web:v2")

    def test_path_components(self):
        assert REGEX.match("myorg/myapp/web:1.0.0")

    def test_separators(self):
        assert REGEX.match("my_app.web--x:tag_1.2-rc")

    def test_uppercase_tag(self):
        # Tags allow uppercase; repository names do not.
        assert REGEX.match("myapp:RC1")


class TestImageRefRejects:
    def test_uppercase_name(self):
        assert not REGEX.match("MyApp")

    def test_spaces_and_punctuation(self):
        assert not REGEX.match("Bad Name!")

    def test_leading_separator(self):
        assert not REGEX.match("-myapp")
        assert not REGEX.match(".myapp")

    def test_empty_tag(self):
        assert not REGEX.match("myapp:")

    def test_registry_host_rejected(self):
        # host:port/name would smuggle a registry address into the name;
        # push targets the in-cluster registry only.
        assert not REGEX.match("ghcr.io:443/myapp")

    def test_digest_rejected(self):
        assert not REGEX.match("myapp@sha256:abc123")


class TestImageRefWiring:
    def test_validator_registered(self):
        validator = canasta._VALIDATORS["image_ref"]
        assert len(validator) == 2, (
            "image_ref has no hint function; keep the tuple at "
            "(regex, message)"
        )
        assert "image name" in validator[1]
