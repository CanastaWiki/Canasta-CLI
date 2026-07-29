"""`gitops_shared_keys` must not contain a credential that unlocks one
specific per-host store.

`gitops push` moves every listed key out of the pushing host's vars.yaml into
hosts/_shared/vars.yaml, where the pushing host's value wins and every other
host then renders it into .env. That is right for an account credential which
is the same everywhere, and wrong for a key that pairs with a per-host
resource: restic_password decrypts the repository named by restic_repository,
which is deliberately per-host, so sharing it hands each host a password for a
repository it does not back up to.
"""
import os

import yaml


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GITOPS_VARS = os.path.join(REPO_ROOT, "roles", "gitops", "vars", "main.yml")


def _shared_keys():
    with open(GITOPS_VARS) as fh:
        return yaml.safe_load(fh)["gitops_shared_keys"]


def test_restic_password_is_not_shared():
    assert "restic_password" not in _shared_keys()


def test_restic_repository_is_not_shared():
    # The pair must stay together: sharing either half without the other
    # leaves a host addressing one repository with another's credentials.
    assert "restic_repository" not in _shared_keys()


def test_account_wide_credentials_are_still_shared():
    keys = _shared_keys()
    for key in ("aws_access_key_id", "aws_secret_access_key"):
        assert key in keys, "%s should stay shared" % key


def test_no_bucket_or_endpoint_identifiers_are_shared():
    for key in _shared_keys():
        for token in ("repository", "bucket", "region", "endpoint", "host"):
            assert token not in key, (
                "%s looks environment-identifying and should stay per-host" % key
            )
