"""A feature-enable flag must be classified host-specific (and so placeholdered
in gitops), or a `config set` of it updates only the live .env: the next gitops
pull re-renders .env from env.template's init-time literal, and
sync_compose_profiles then derives the profile off — silently disabling the
feature."""

import os

import yaml


REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
CLASSIFIER = os.path.join(REPO_ROOT, "vars", "secret_classification.yml")


def _host_specific_keys():
    with open(CLASSIFIER) as f:
        return yaml.safe_load(f)["canasta_host_specific_nonsecret"]


def test_profile_feature_flags_are_placeholdered():
    keys = _host_specific_keys()
    for flag in (
        "CANASTA_ENABLE_CROWDSEC",
        "CANASTA_ENABLE_ELASTICSEARCH",
        "CANASTA_ENABLE_OBSERVABILITY",
        "CANASTA_ENABLE_VARNISH",
    ):
        assert flag in keys, (
            "%s must be classified host-specific so gitops placeholders it and "
            "'config set' persists to the gitops source; otherwise a pull "
            "renders it back to the init-time literal and the profile derives "
            "off" % flag
        )
