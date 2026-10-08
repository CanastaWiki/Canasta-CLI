"""The Docker image reclaim removes images Compose built that no instance uses.

Candidates are the images labeled with the compose project of an instance
directory on the host. An image any instance's compose project references,
or any instance pins, is kept, and nothing built is removed when an
instance's references can't be read.
"""

import os

import pytest
import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
RECLAIM = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "image_reclaim.yml")


def _flatten(tasks):
    out = []
    for task in tasks:
        out.append(task)
        for key in ("block", "rescue", "always"):
            if key in task:
                out.extend(_flatten(task[key]))
    return out


def _task(name):
    with open(RECLAIM) as f:
        tasks = _flatten(yaml.safe_load(f))
    for task in tasks:
        if task.get("name") == name:
            return task
    raise AssertionError("task not found: %s" % name)


def _render(expr, **variables):
    templar = Templar(loader=DataLoader(), variables=variables)
    return templar.template(trust_as_template(expr))


def _results(*rows):
    """Registered loop results: (rc, stdout_lines) per instance."""
    return {"results": [{"rc": rc, "stdout_lines": lines}
                        for rc, lines in rows]}


def _select(built, referenced, pinned=()):
    fact = _task("Select the built images no instance references")[
        "ansible.builtin.set_fact"]
    variables = {
        "_reclaim_built_images": built,
        "_reclaim_compose_images": referenced,
        "_reclaim_pinned": list(pinned),
    }
    removable = _render(fact["_reclaim_built_removable"], **variables)
    skipped = _render(fact["_reclaim_built_skipped"], **variables)
    return list(removable), str(skipped).strip() == "True"


class TestSelection:
    def test_an_unreferenced_built_image_is_removable(self):
        removable, skipped = _select(
            _results((0, ["canasta-esip:custom"])),
            _results((0, ["ghcr.io/canastawiki/canasta:3.5.26",
                          "docker.io/library/mariadb:11.4"])))
        assert removable == ["canasta-esip:custom"]
        assert not skipped

    def test_a_referenced_built_image_is_kept(self):
        # Covers a stopped instance: compose config still names its image
        # even though `canasta stop` removed the containers.
        removable, _ = _select(
            _results((0, ["canasta-pge:custom"])),
            _results((0, ["canasta-pge:custom", "redis:7.4-alpine"])))
        assert removable == []

    def test_a_pinned_image_is_kept(self):
        removable, _ = _select(
            _results((0, ["canasta-web-site:local"])),
            _results((0, [])),
            pinned=["canasta-web-site:local"])
        assert removable == []

    def test_references_from_any_instance_protect_an_image(self):
        removable, _ = _select(
            _results((0, ["a:custom"]), (0, ["b:custom"])),
            _results((0, ["b:custom"]), (0, ["a:custom"])))
        assert removable == []

    def test_untagged_rows_are_left_to_the_dangling_prune(self):
        removable, _ = _select(
            _results((0, ["<none>:<none>", "", "x:custom"])),
            _results((0, [])))
        assert removable == ["x:custom"]

    @pytest.mark.parametrize("failed", ["references", "built"])
    def test_an_unreadable_instance_keeps_every_built_image(self, failed):
        built = _results((0, ["a:custom"]), (0, ["b:custom"]))
        refs = _results((0, []), (0, []))
        target = refs if failed == "references" else built
        target["results"][1]["rc"] = 1
        removable, skipped = _select(built, refs)
        assert removable == []
        assert skipped

    def test_no_local_instances_means_nothing_to_do(self):
        removable, skipped = _select(_results(), _results())
        assert removable == []
        assert not skipped


class TestWiring:
    def test_candidates_come_from_each_local_instance_compose_project(self):
        task = _task("List the images Compose built for those instances")
        cmd = task["ansible.builtin.command"]["cmd"]
        assert "label=com.docker.compose.project=" in cmd
        assert "_reclaim_compose_path | basename | lower" in cmd
        assert task["loop"] == "{{ _reclaim_local_paths }}"

    def test_references_include_every_profile(self):
        task = _task("List the images each instance's compose project references")
        assert task["ansible.builtin.command"]["cmd"] == (
            "{{ compose_command | default('docker compose') }} "
            "--profile '*' config --images")
        assert task["ansible.builtin.command"]["chdir"] == (
            "{{ _reclaim_compose_path }}")
        assert task["failed_when"] is False

    def test_only_instance_directories_present_on_the_host_are_used(self):
        fact = _task("Note the instance directories present here")[
            "ansible.builtin.set_fact"]["_reclaim_local_paths"]
        stat_result = lambda path, isdir: {  # noqa: E731
            "stat": {"exists": isdir, "isdir": isdir} if isdir
            else {"exists": False},
            "_reclaim_dir": {"key": path, "value": {"path": path}},
        }
        paths = _render(fact, _reclaim_dirs={"results": [
            stat_result("/home/a/site", True),
            stat_result("/home/b/gone", False),
            stat_result("/home/a/site", True),
        ]})
        assert list(paths) == ["/home/a/site"]

    def test_removal_is_gated_on_dry_run_and_never_forced(self):
        task = _task("Remove built images no instance references")
        assert "dry_run" in str(task["when"])
        cmd = task["ansible.builtin.command"]["cmd"]
        assert "image rm" in cmd and "--force" not in cmd and " -f" not in cmd
        assert task["loop"] == "{{ _reclaim_built_removable }}"

    def test_built_images_go_before_the_dangling_prune(self):
        with open(RECLAIM) as f:
            names = [t.get("name") for t in _flatten(yaml.safe_load(f))]
        assert names.index("Remove built images no instance references") < (
            names.index("Prune dangling images (sidecar rebuild orphans)"))

    def test_the_report_counts_built_images(self):
        msg = _task("Report Docker reclaim")["ansible.builtin.debug"]["msg"]
        assert "_reclaim_built_removed" in msg
        assert "_reclaim_built_skipped" in msg
