"""The uninstall command's package choices and its playbook's branches agree."""

import os

from test_validate_definitions import validate_definitions


class TestDispatchChecks:
    REPO_ROOT = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "..")
    )

    def _playbook(self, name):
        return os.path.join(self.REPO_ROOT, "playbooks", name)

    def test_uninstall_choices_and_branches_agree(self):
        param = {"name": "packages", "choices": ["k8s"]}
        assert validate_definitions.check_dispatch(
            {}, param, self._playbook("uninstall.yml")) == []
