"""`canasta extension|skin add` refuses Kubernetes instances: the commands
are declared Compose-only, and the orchestrator restart is guarded against a
Kubernetes instance."""

import os

import yaml

from test_extension_add import ADD, REPO_ROOT, _load, _walk


class TestKubernetesGuard:
    def test_declared_compose_only(self):
        # canasta.py refuses the command on Kubernetes from this key.
        with open(os.path.join(REPO_ROOT, "meta", "command_definitions.yml")) as f:
            commands = {c["name"]: c for c in yaml.safe_load(f)["commands"]}
        for name in ['extension_add', 'skin_add']:
            assert commands[name].get("orchestrators") == ['compose'], name

    def test_restart_skipped_on_kubernetes(self):
        # The Compose restart must not run on a Kubernetes instance.
        for t in _walk(_load(ADD)):
            start = t.get("ansible.builtin.include_role") or {}
            if isinstance(start, dict) and start.get("tasks_from") == "start.yml":
                assert t.get("when") == "not (instance_is_k8s | bool)", (
                    "the orchestrator restart must be guarded against "
                    "kubernetes instances")
