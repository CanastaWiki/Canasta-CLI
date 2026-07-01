"""A feature-enable flag must be a gitops placeholder key, or a `config set`
of it updates only the live .env: the next gitops pull re-renders .env from
env.template's init-time literal, and sync_compose_profiles then derives the
profile off — silently disabling the feature."""

import os

import yaml


REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
GITOPS_VARS = os.path.join(REPO_ROOT, "roles", "gitops", "vars", "main.yml")


def _placeholder_keys():
    with open(GITOPS_VARS) as f:
        return yaml.safe_load(f)["gitops_placeholder_keys"]


def test_profile_feature_flags_are_placeholder_keys():
    keys = _placeholder_keys()
    for flag in (
        "CANASTA_ENABLE_CROWDSEC",
        "CANASTA_ENABLE_ELASTICSEARCH",
        "CANASTA_ENABLE_OBSERVABILITY",
        "CANASTA_ENABLE_VARNISH",
    ):
        assert flag in keys, (
            "%s must be a gitops placeholder key so 'config set' of it persists "
            "to the gitops source; otherwise a pull renders it back to the "
            "init-time literal and the profile derives off" % flag
        )
