"""The canasta-docker wrapper's sanitized kubeconfig copy is owner-only.

The copy carries cluster credentials, so it must not be readable by other
local users, including when a copy from an earlier run is 0644.
"""

import os

import test_canasta_docker as shared
from test_canasta_docker import (
    _check_existing_0644_copies_are_tightened, _check_new_copies_are_0600,
    _credential_home)

_cleanup_run_dry_tmpdirs = shared._cleanup_run_dry_tmpdirs


def _home_with_kubeconfig():
    home = _credential_home()
    os.makedirs(os.path.join(home, ".kube"))
    with open(os.path.join(home, ".kube", "config"), "w") as f:
        f.write(
            "clusters:\n"
            "- cluster:\n"
            "    server: https://127.0.0.1:6443\n"
            "  name: local\n"
        )
    return home


class TestCredentialCopiesOwnerOnly:
    NAMES = (".kube-config",)

    def test_new_copies_are_0600(self):
        _check_new_copies_are_0600(self.NAMES, _home_with_kubeconfig())

    def test_existing_0644_copies_are_tightened(self):
        _check_existing_0644_copies_are_tightened(
            self.NAMES, _home_with_kubeconfig())
