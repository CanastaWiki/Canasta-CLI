"""The shared image reclaim keeps the containerd images a stopped Kubernetes
instance still needs.

On a k3s host the reclaim selects the containerd images nothing on the host
references or pins, removes them one by one with elevated crictl, and
reports a selection that could not run as a failure. The containerd removal
and the in-cluster registry garbage collection are skipped on a dry run.
"""

import pytest

from test_image_reclaim import _by_name, _commands


@pytest.mark.parametrize("name", [
    "Remove containerd images nothing on this host needs",
    "Garbage-collect the in-cluster registry",
])
def test_every_destructive_task_is_gated_on_dry_run(name):
    cond = str(_by_name(name).get("when"))
    assert "reclaim_dry_run" in cond, name
    assert "not (" in cond, name


class TestContainerdReclaimIsPinAware:
    """`crictl rmi --prune` decides by pod references alone, and `canasta
    stop` scales a Kubernetes instance to zero pods. A blanket prune
    therefore removed the stopped instance's own image — the same defect
    the Docker path had, on the other orchestrator.

    crictl cannot express an exclusion, so the removal set is computed
    here and the images are removed individually.
    """

    def test_the_blanket_prune_is_gone(self):
        for cmd in _commands():
            assert "rmi --prune" not in cmd, (
                "--prune cannot exclude a pinned image: %s" % cmd
            )

    def test_selection_excludes_in_use_pinned_and_sandbox_images(self):
        script = _by_name(
            "Select the containerd images nothing on this host needs"
        )["ansible.builtin.shell"]["cmd"]
        assert "in_use" in script and "imageRef" in script, (
            "images a container references must be kept"
        )
        assert "pinned.intersection" in script, (
            "images an instance on this host records must be kept"
        )
        assert 'img.get("pinned")' in script, (
            "containerd's own pinned images (the sandbox image) must be kept"
        )

    def test_pins_are_passed_in_from_the_registry_derived_set(self):
        task = _by_name(
            "Select the containerd images nothing on this host needs"
        )
        assert "_reclaim_pinned" in str(task.get("environment"))

    def test_prefers_k3s_crictl_over_the_bare_binary(self):
        cmd = _by_name("Resolve the crictl invocation")[
            "ansible.builtin.shell"]["cmd"]
        assert cmd.index("k3s crictl") < cmd.index("echo 'crictl'"), (
            "bare crictl cannot find the k3s containerd endpoint"
        )

    def test_containerd_tasks_run_elevated(self):
        for name in ("Resolve the crictl invocation",
                     "Select the containerd images nothing on this host needs",
                     "Remove containerd images nothing on this host needs"):
            assert _by_name(name).get("become") is True, name

    def test_a_failed_selection_is_reported_as_a_failure(self):
        cond = str(_by_name(
            "Report a containerd reclaim that could not run").get("when"))
        assert "_reclaim_crictl_candidates.rc" in cond and "!= 0" in cond

    def test_the_success_report_does_not_claim_zero_on_failure(self):
        cond = str(_by_name("Report containerd reclaim").get("when"))
        assert "_reclaim_crictl_candidates.rc" in cond and "== 0" in cond
