"""Maintenance-script config keys reach the Kubernetes web pods.

Each key in the "Maintenance scripts" group of canasta_known_keys must be in
_k8s_pod_env_allowlist, which curates the .env keys the env ConfigMap
carries to the web pods.
"""

from test_maintenance_env_forwarding import _k8s_pod_env_allowlist, _maintenance_keys


class TestMaintenanceEnvForwarding:
    def test_forwarded_to_k8s_pod_env(self):
        missing = _maintenance_keys() - _k8s_pod_env_allowlist()
        assert not missing, (
            "maintenance keys recognized by config set but NOT in the K8s "
            "_k8s_pod_env_allowlist (they'd never reach the pods): %s"
            % sorted(missing))
