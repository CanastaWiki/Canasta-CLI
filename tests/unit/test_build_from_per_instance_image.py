"""Each --build-from Compose instance builds to its own image tag.

A tag shared by every build-from instance on a host is taken over by
whichever instance built last; the others run that build once their web
containers are recreated. Compose instances therefore build to
canasta:local-<id>. Kubernetes keeps canasta:local, the name its
values.yaml and in-cluster registry pushes refer to.

  - the build names its output after the instance;
  - upgrade moves a rebuilt instance still naming canasta:local onto its
    own tag (and into env.template on a gitops instance);
  - image prune removes the tag of an instance no longer registered.
"""

import os

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
BUILD_FROM = os.path.join(
    REPO_ROOT, "roles", "imagebuild", "tasks", "build_from_source.yml")
IMAGE_TAG = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "upgrade_image_tag.yml")
RECLAIM = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "_image_reclaim_runtime.yml")


def _flatten(tasks):
    out = []
    for task in tasks:
        out.append(task)
        for key in ("block", "rescue", "always"):
            if key in task:
                out.extend(_flatten(task[key]))
    return out


def _tasks(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _task(path, name):
    for task in _flatten(_tasks(path)):
        if task.get("name") == name:
            return task
    raise AssertionError("task not found: %s" % name)


def _render(expr, **variables):
    templar = Templar(loader=DataLoader(), variables=variables)
    return templar.template(trust_as_template(expr))


def _when(task, **variables):
    conds = task["when"]
    conds = conds if isinstance(conds, list) else [conds]
    return all(
        str(_render("{{ %s }}" % c, **variables)).strip() == "True"
        for c in conds)


class TestBuildName:
    def _name(self, **variables):
        fact = _task(BUILD_FROM, "Name the image this build produces")[
            "ansible.builtin.set_fact"]["_build_image_name"]
        return str(_render(fact, **variables)).strip()

    def test_compose_names_the_image_after_the_instance(self):
        assert self._name(instance_is_k8s=False, id="wiki") == (
            "canasta:local-wiki")

    def test_upgrade_names_it_after_the_instance_being_upgraded(self):
        assert self._name(instance_is_k8s=False, instance_id="b",
                          id="a") == "canasta:local-b"

    def test_kubernetes_keeps_the_shared_name(self):
        assert self._name(instance_is_k8s=True, id="wiki") == "canasta:local"

    def test_the_name_is_set_before_anything_builds(self):
        names = [t.get("name") for t in _tasks(BUILD_FROM)]
        first = names.index("Name the image this build produces")
        assert first < names.index("Build CanastaBase locally")
        assert first < names.index("Build Canasta image from source")

    def test_the_build_and_the_output_fact_use_the_name(self):
        build = _task(BUILD_FROM, "Build Canasta image from source")
        assert "-t {{ _build_image_name }} ." in " ".join(
            build["ansible.builtin.command"]["cmd"].split())
        fact = _task(BUILD_FROM, "Set built image fact")
        assert fact["ansible.builtin.set_fact"]["_built_image"] == (
            "{{ _build_image_name }}")

    def test_the_collision_guard_compares_with_the_name(self):
        guard = _task(
            BUILD_FROM,
            "Fail if the base image collides with the build-from output tag")
        assert guard["when"] == "_effective_base_image == _build_image_name"


class TestUpgradeMovesOffTheSharedTag:
    BLOCK = "Point a rebuilt Compose instance at its own image tag"

    def _applies(self, image="canasta:local", k8s=False, rebuilt=True,
                 built="canasta:local-wiki"):
        return _when(
            _task(IMAGE_TAG, self.BLOCK),
            instance_is_k8s=k8s,
            _upgrade_rebuild=rebuilt,
            _built_image=built,
            _mig={"env": {"CANASTA_IMAGE": image},
                  "env_present": {"CANASTA_IMAGE": image != ""}})

    def test_a_rebuilt_instance_on_the_shared_tag_moves(self):
        assert self._applies()

    def test_an_instance_not_rebuilt_keeps_its_image(self):
        # Its source is gone, so its own tag was never built.
        assert not self._applies(rebuilt=False)

    def test_kubernetes_is_left_alone(self):
        assert not self._applies(k8s=True, built="canasta:local")

    def test_an_instance_already_on_its_own_tag_is_left_alone(self):
        assert not self._applies(image="canasta:local-wiki")

    def test_an_operator_chosen_image_is_left_alone(self):
        assert not self._applies(image="example.com/custom:1")

    def test_env_and_gitops_template_get_the_built_tag(self):
        env = _task(IMAGE_TAG, "Update CANASTA_IMAGE to the instance's own image tag")
        assert env["canasta_env"]["key"] == "CANASTA_IMAGE"
        assert env["canasta_env"]["value"] == "{{ _built_image }}"
        tmpl = _task(IMAGE_TAG, "Persist the instance's image tag to env.template")
        assert tmpl["ansible.builtin.lineinfile"]["line"] == (
            "CANASTA_IMAGE={{ _built_image }}")
        stage = _task(IMAGE_TAG, "Stage the updated env.template")
        assert stage["ansible.builtin.command"]["cmd"] == "git add env.template"

    def test_runs_after_the_rebuild_is_published(self):
        with open(os.path.join(REPO_ROOT, "roles", "upgrade", "tasks",
                               "main.yml")) as f:
            names = [t.get("name") for t in yaml.safe_load(f)]
        assert names.index("Publish rebuilt image for the orchestrator") < (
            names.index("Update configured image tag"))


class TestPruneRemovesUnregisteredBuilds:
    def _removable(self, source, instances, pinned=(), ghcr=()):
        fact = _task(RECLAIM, "Select the tags no instance on this host is pinned to")
        variables = {
            "_reclaim_images": {"stdout_lines": list(ghcr)},
            "_reclaim_source_images": {"stdout_lines": list(source)},
            "_reclaim_instances": {"instances": {i: {} for i in instances}},
            "_reclaim_pinned": list(pinned),
        }
        for key, value in fact["vars"].items():
            variables[key] = _render(value, **variables)
        return list(_render(
            fact["ansible.builtin.set_fact"]["_reclaim_removable"],
            **variables))

    def test_a_deleted_instance_build_is_removable(self):
        assert self._removable(
            ["canasta:local-gone", "canasta:local-kept"], ["kept"]) == [
            "canasta:local-gone"]

    def test_podman_names_are_matched_without_localhost(self):
        assert self._removable(["localhost/canasta:local-gone"], []) == [
            "canasta:local-gone"]

    def test_a_pinned_build_is_kept_even_if_unregistered(self):
        assert self._removable(
            ["canasta:local-other"], ["wiki"],
            pinned=["canasta:local-other"]) == []

    def test_other_images_are_not_candidates(self):
        assert self._removable(
            ["canasta:local", "canasta-base:local", "canasta-wiki-x:local",
             "canasta-xdebug:local", "mariadb:11.4", "<none>:<none>"],
            []) == []

    def test_ghcr_selection_is_unchanged(self):
        assert self._removable(
            [], [], pinned=["ghcr.io/canastawiki/canasta:3.5.29"],
            ghcr=["ghcr.io/canastawiki/canasta:3.5.28",
                  "ghcr.io/canastawiki/canasta:3.5.29"]) == [
            "ghcr.io/canastawiki/canasta:3.5.28"]
