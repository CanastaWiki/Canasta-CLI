"""Mock AnsibleModule for testing run_module() functions."""

import json
from unittest.mock import MagicMock, patch


class MockAnsibleModule:
    """Simulates AnsibleModule for unit testing Ansible custom modules."""

    def __init__(self, params):
        self.params = params
        self.check_mode = params.pop("_check_mode", False)
        self._result = None
        self._failed = False
        self._fail_msg = None

    def exit_json(self, **kwargs):
        self._result = kwargs
        raise SystemExit(0)

    def fail_json(self, **kwargs):
        self._failed = True
        self._fail_msg = kwargs.get("msg", "")
        self._result = kwargs
        raise SystemExit(1)


def run_module_with_params(module, params):
    """Run a module's run_module() with mocked AnsibleModule.

    Returns (result_dict, failed_bool, fail_msg).
    """
    mock = MockAnsibleModule(params.copy())

    with patch.object(module, "AnsibleModule", return_value=mock):
        try:
            module.run_module()
        except SystemExit:
            pass

    return mock._result or {}, mock._failed, mock._fail_msg or ""
