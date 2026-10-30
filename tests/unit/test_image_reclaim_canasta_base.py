"""image prune removes canasta-base:local once nothing builds on it.

A --build-from with a CanastaBase source builds canasta-base:local, then
the instance's canasta:local-<id> on top of it. The base carries no
compose project label and is not a canasta:local-<id> tag, so once the
last such instance was deleted nothing removed it. It is now removed when
no registered instance's built image remains and no Kubernetes instance
is on the host, whose build copies are reported instead.
"""

import os

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
RECLAIM = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "_image_reclaim_runtime.yml")
NAME = "Select the tags no instance on this host is pinned to"


def _task():
    with open(RECLAIM) as f:
        return next(t for t in yaml.safe_load(f) if t.get("name") == NAME)


def _render(expr, **variables):
    templar = Templar(loader=DataLoader(), variables=variables)
    return templar.template(trust_as_template(expr))


def _removable(source, instances=(), k8s=(), pinned=()):
    task = _task()
    variables = {
        "_reclaim_images": {"stdout_lines": []},
        "_reclaim_source_images": {"stdout_lines": list(source)},
        "_reclaim_instances": {"instances": dict(
            {i: {"orchestrator": "compose"} for i in instances},
            **{i: {"orchestrator": "kubernetes"} for i in k8s})},
        "_reclaim_pinned": list(pinned),
    }
    for key, value in task["vars"].items():
        variables[key] = _render(value, **variables)
    return list(_render(
        task["ansible.builtin.set_fact"]["_reclaim_removable"], **variables))


def test_base_is_removed_after_the_last_built_instance_is_deleted():
    # Last, after the images built on it.
    assert _removable(["canasta-base:local", "canasta:local-gone",
                       "canasta:local-old", "mariadb:11.4"])[-1] == (
        "canasta-base:local")
    assert sorted(_removable(["canasta-base:local", "canasta:local-gone",
                              "mariadb:11.4"])) == [
        "canasta-base:local", "canasta:local-gone"]


def test_podman_base_is_removed_by_its_short_name():
    assert _removable(["localhost/canasta-base:local"]) == [
        "canasta-base:local"]


def test_base_is_kept_while_a_registered_instance_is_built_on_it():
    assert _removable(["canasta-base:local", "canasta:local-site"],
                      instances=["site"]) == []


def test_base_is_kept_on_a_host_with_a_kubernetes_instance():
    assert _removable(["canasta-base:local"], k8s=["k"]) == []


def test_a_pinned_base_is_kept():
    assert _removable(["canasta-base:local"],
                      pinned=["canasta-base:local"]) == []


def test_an_instance_on_a_released_image_does_not_keep_the_base():
    assert _removable(["canasta-base:local"], instances=["site"]) == [
        "canasta-base:local"]
