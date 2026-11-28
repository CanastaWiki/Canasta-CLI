"""`canasta create --canasta-base` runs the canasta-base release.

Without --build-from it resolves to the canasta-base image of the CLI's
Canasta version, on both orchestrators; --canasta-image still names any
other image, and the two cannot be combined.
"""

import os
import re
import sys

import jinja2
import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "filter_plugins"))

import canasta
from canasta_images import FilterModule

DEFAULT = "ghcr.io/canastawiki/canasta:3.6.1"
BASE = "ghcr.io/canastawiki/canasta-base:3.6.1"


def _load(*parts):
    with open(os.path.join(REPO_ROOT, *parts)) as f:
        return yaml.safe_load(f)


def _param(name):
    commands = _load("meta", "command_definitions.yml")["commands"]
    create = next(c for c in commands if c["name"] == "create")
    return next(p for p in create["parameters"] if p["name"] == name), create


def _jinja():
    env = jinja2.Environment()
    env.filters.update(FilterModule().filters())
    env.filters["ternary"] = lambda c, a, b: a if c else b
    env.filters["bool"] = lambda v: str(v).lower() in ("true", "1", "yes")
    env.filters["regex_replace"] = (
        lambda s, pat, repl="": re.sub(pat, repl, str(s)))
    return env


def _chosen_image(**variables):
    task = next(t for t in _load("roles", "create", "tasks", "main.yml")
                if t.get("name") == "Set base image")
    expr = task["ansible.builtin.set_fact"]["_base_image"]
    return _jinja().from_string(expr).render(
        canasta_default_image=DEFAULT, **variables).strip()


class TestDefinition:
    def test_flag_is_a_bool_exclusive_with_canasta_image(self):
        param, _ = _param("canasta_base")
        assert param["type"] == "bool"
        assert param["mutual_exclusion"] == "canasta_image"

    def test_combining_with_canasta_image_is_refused(self):
        _, create = _param("canasta_base")
        values = {"canasta_base": True, "canasta_image": "example/x:1"}
        errors = canasta._validate_mutual_exclusion(create, values.get)
        assert errors and "--canasta-base" in errors[0][1]

    def test_with_build_from_is_allowed(self):
        _, create = _param("canasta_base")
        values = {"canasta_base": True, "build_from": "/src"}
        assert canasta._validate_mutual_exclusion(create, values.get) == []


class TestChosenImage:
    def test_default_is_canasta(self):
        assert _chosen_image() == DEFAULT

    def test_canasta_base_is_the_matching_release(self):
        assert _chosen_image(canasta_base="true") == BASE

    def test_canasta_image_is_used_as_given(self):
        assert _chosen_image(canasta_image="example/x:1") == "example/x:1"

    def test_build_from_uses_the_built_image(self):
        assert _chosen_image(build_from="/src", _built_image="canasta:local-a",
                             canasta_base="true") == "canasta:local-a"
