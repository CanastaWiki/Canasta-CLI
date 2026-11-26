"""`config set --secret` dispatches to the Kubernetes secret writer.

On Kubernetes the value goes to config/secrets.env, and --web lists the key
in config/secrets-web.
"""

import os

from test_compose_secrets import CONFIG_TASKS, ORCH_TASKS, _read


class TestDispatch:
    def test_set_dispatches_to_kubernetes(self):
        body = _read(os.path.join(ORCH_TASKS, "config_set_secret.yml"))
        assert "_set_secret_kubernetes.yml" in body

    def test_kubernetes_still_writes_secrets_env(self):
        body = _read(os.path.join(CONFIG_TASKS, "_set_secret_kubernetes.yml"))
        assert "config/secrets.env" in body
        assert "config/secrets-web" in body
