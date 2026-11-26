"""image prune removes the canasta-base builds nothing builds on any more.

A --build-from with a canasta-base source builds canasta-base:local-<id>,
then the instance's canasta:local-<id> on top of it; both are removed once
the instance is no longer registered, the base after the image built on
it. Older CLIs built one canasta-base:local for the host: it is removed
when no registered instance's built image remains and no Kubernetes
instance is on the host, whose build copies are reported instead.
Published canasta-base release tags no instance is pinned to are removed
like canasta ones.
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


def _removable(source, instances=(), k8s=(), pinned=(), published_base=()):
    task = _task()
    variables = {
        "_reclaim_images": {"stdout_lines": []},
        "_reclaim_base_images": {"stdout_lines": list(published_base)},
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


def test_a_pinned_base_is_kept():
    assert _removable(["canasta-base:local"],
                      pinned=["canasta-base:local"]) == []


def test_an_instance_on_a_released_image_does_not_keep_the_base():
    assert _removable(["canasta-base:local"], instances=["site"]) == [
        "canasta-base:local"]


def test_a_per_instance_base_of_a_deleted_instance_is_removed_last():
    assert _removable(["canasta-base:local-gone", "canasta:local-gone",
                       "mariadb:11.4"]) == [
        "canasta:local-gone", "canasta-base:local-gone"]


def test_a_per_instance_base_is_kept_while_its_instance_is_registered():
    assert _removable(["canasta-base:local-site", "canasta:local-site"],
                      instances=["site"]) == []
    assert _removable(["canasta-base:local-k"], k8s=["k"]) == []


def test_podman_per_instance_base_is_removed_by_its_short_name():
    assert _removable(["localhost/canasta-base:local-gone"]) == [
        "canasta-base:local-gone"]


def test_an_unpinned_canasta_base_release_is_removed():
    old = "ghcr.io/canastawiki/canasta-base:3.6.0"
    new = "ghcr.io/canastawiki/canasta-base:3.6.1"
    assert _removable([], published_base=[old, new], pinned=[new]) == [old]
