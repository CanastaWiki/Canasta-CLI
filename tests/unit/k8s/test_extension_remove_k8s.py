"""extension and skin remove are declared Compose-only.

canasta.py refuses both commands on Kubernetes because their command
definitions list only the compose orchestrator.
"""

import yaml
from test_extension_remove import DEFS


class TestKubernetesGuard:
    def test_declared_compose_only(self):
        # canasta.py refuses the command on Kubernetes from this key.
        with open(DEFS) as f:
            commands = {c["name"]: c for c in yaml.safe_load(f)["commands"]}
        for name in ['extension_remove', 'skin_remove']:
            assert commands[name].get("orchestrators") == ['compose'], name
