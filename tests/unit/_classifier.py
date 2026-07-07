"""Shared test helper: render the REAL canonical secret-classifier regex from
vars/secret_classification.yml, using the file's own Jinja expression rather
than a hand-rebuilt mirror. A shape change in the vars file then flows into the
tests automatically — the exact drift the canonical classifier exists to end.
"""
import os

import yaml
from jinja2 import Environment

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CLASSIFIER = os.path.join(REPO_ROOT, "vars", "secret_classification.yml")


def classifier():
    """The parsed vars/secret_classification.yml dict."""
    with open(CLASSIFIER) as fh:
        return yaml.safe_load(fh)


def secret_key_regex(cls=None):
    """Render canasta_secret_key_regex exactly as Ansible resolves it."""
    cls = cls or classifier()
    return (
        Environment(autoescape=False)
        .from_string(cls["canasta_secret_key_regex"])
        .render(**cls)
        .strip()
    )
