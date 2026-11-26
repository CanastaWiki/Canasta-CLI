"""config set refuses TLS_MODE_HOSTS on a Kubernetes instance."""

import yaml

from test_tls_mode_hosts_validation import VALIDATE_VALUE


def test_config_set_refuses_it_on_kubernetes():
    with open(VALIDATE_VALUE) as f:
        tasks = yaml.safe_load(f)
    k8s_block = next(t for t in tasks if t.get("name", "").startswith("Block the edge's TLS keys"))
    assert "'TLS_MODE_HOSTS'" in " ".join(k8s_block["when"])
