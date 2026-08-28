"""The shared image reclaim keeps images a stopped instance still needs.

The removal loop relied on Docker refusing to delete an in-use image. That
guard only covers images a container object references, and `canasta stop`
runs `docker compose down`, which removes the containers. A stopped instance
therefore had nothing holding its image, and `canasta upgrade --purge`
deleted the image the upgrade had just refreshed it to.

The pins recorded in each instance's .env are the durable record, so they
decide what stays.
"""

import os

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
TASKS = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "image_reclaim.yml")


def _load():
    with open(TASKS) as f:
        return yaml.safe_load(f)


def _flatten(tasks):
    """Every task, including those nested in a block."""
    out = []
    for task in tasks:
        out.append(task)
        for key in ("block", "rescue", "always"):
            if key in task:
                out.extend(_flatten(task[key]))
    return out


def _by_name(name):
    for task in _flatten(_load()):
        if task.get("name") == name:
            return task
    raise AssertionError("task not found: %s" % name)


def test_removal_set_excludes_pinned_tags():
    fact = _by_name("Select the tags no instance on this host is pinned to")
    expr = str(fact["ansible.builtin.set_fact"]["_reclaim_removable"])
    assert "difference(_reclaim_pinned)" in expr


def test_pins_come_from_every_instance_on_the_host_not_from_containers():
    read = _by_name("Read the images each instance is pinned to")
    assert read["canasta_env"]["state"] == "read_all"
    query = _by_name("Find every registered instance")
    assert query["canasta_registry"]["state"] == "query_all"
    # Not host-filtered: --host is often an SSH alias that does not match the
    # registry's canonical user@fqdn, which silently emptied the pin set.
    assert "filter_host" not in query["canasta_registry"]
    # The registry lives on the controller, never on the target.
    assert query["delegate_to"] == "canasta_controller"


def test_removal_loops_over_the_filtered_set():
    remove = _by_name("Remove Canasta image tags no instance needs")
    assert remove["loop"] == "{{ _reclaim_removable }}"
    # No --force, so a running instance's tag survives a missed pin too.
    assert "--force" not in remove["ansible.builtin.command"]["cmd"]
    assert " -f " not in remove["ansible.builtin.command"]["cmd"]


def _commands():
    """Every shell/command string the reclaim runs."""
    out = []
    for task in _flatten(_load()):
        for module in ("ansible.builtin.command", "ansible.builtin.shell"):
            if module in task:
                out.append(str(task[module].get("cmd", "")))
    return out


def test_no_command_touches_volumes():
    for cmd in _commands():
        assert "volume" not in cmd, cmd
        assert "--volumes" not in cmd, cmd
        assert "system prune" not in cmd, cmd


def test_every_destructive_task_is_gated_on_dry_run():
    destructive = [
        "Remove Canasta image tags no instance needs",
        "Prune dangling images (sidecar rebuild orphans)",
        "Remove containerd images nothing on this host needs",
        "Garbage-collect the in-cluster registry",
    ]
    for name in destructive:
        cond = str(_by_name(name).get("when"))
        assert "reclaim_dry_run" in cond, name
        assert "not (" in cond, name


def test_registry_gc_include_is_not_wrapped_in_a_conditional_block():
    """Block scoping breaks set_fact propagation out of an included file."""
    for task in _load():
        if "block" not in task:
            continue
        for child in _flatten(task["block"]):
            assert "ansible.builtin.include_tasks" not in child, task.get("name")


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


class TestPinSetCoversEveryImageKey:
    """CANASTA_IMAGE alone left an instance's Caddy and Elasticsearch
    images unprotected."""

    def test_all_image_valued_env_keys_are_pinned(self):
        keys = _by_name("Name the .env keys that hold an image reference")[
            "ansible.builtin.set_fact"]["_reclaim_image_keys"]
        assert set(keys) >= {
            "CANASTA_IMAGE",
            "CANASTA_CADDY_IMAGE",
            "CANASTA_ELASTICSEARCH_IMAGE",
        }

    def test_pins_are_read_from_every_instance_on_the_host(self):
        read = _by_name("Read the images each instance is pinned to")
        assert read["canasta_env"]["state"] == "read_all"
