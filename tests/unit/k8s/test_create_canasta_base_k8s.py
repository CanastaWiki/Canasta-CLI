"""`canasta create --canasta-base` on Kubernetes writes the chosen
canasta-base image into the Helm values as image.repository and
image.tag."""

import os

import yaml

from test_create_canasta_base import (
    BASE,
    DEFAULT,
    REPO_ROOT,
    _jinja,
)


class TestKubernetesValues:
    def test_values_name_the_chosen_image(self):
        path = os.path.join(REPO_ROOT, "roles", "orchestrator", "templates",
                            "k8s_values.yaml.j2")
        with open(path) as f:
            text = f.read()
        head = text.split("domains:", 1)[0]
        out = yaml.safe_load(_jinja().from_string(head).render(
            id="t", canasta_default_image=DEFAULT, _base_image=BASE))
        assert out["image"]["repository"] == "ghcr.io/canastawiki/canasta-base"
        assert out["image"]["tag"] == "3.6.1"
