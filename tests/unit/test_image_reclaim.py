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
    read = _by_name("Read the image each instance is pinned to")
    assert read["canasta_env"]["key"] == "CANASTA_IMAGE"
    query = _by_name("Find the instances registered on this host")
    assert query["canasta_registry"]["state"] == "query_all"
    assert query["canasta_registry"]["filter_host"] == "{{ _reclaim_host_name }}"
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
        "Prune containerd images no pod references",
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


class TestContainerdPruneActuallyRuns:
    """k3s installs a bare crictl on PATH whose config only root can read,
    and the containerd socket is root-owned. An unprivileged run of the
    wrong binary exits non-zero having pruned nothing, and reported the
    same "0 image(s) removed" as a host with nothing to reclaim."""

    def _task(self):
        return _by_name("Prune containerd images no pod references")

    def test_prefers_k3s_crictl_over_the_bare_binary(self):
        cmd = self._task()["ansible.builtin.shell"]["cmd"]
        k3s_at = cmd.index("k3s crictl rmi")
        bare_at = cmd.index("then crictl rmi")
        assert k3s_at < bare_at, (
            "bare crictl cannot find the k3s containerd endpoint; the k3s "
            "branch must be tried first"
        )

    def test_runs_elevated(self):
        assert self._task().get("become") is True, (
            "the containerd socket is root-owned"
        )

    def test_a_failed_prune_is_reported_as_a_failure(self):
        report = _by_name("Report a containerd prune that could not run")
        cond = str(report.get("when"))
        assert "_reclaim_crictl.rc" in cond and "!= 0" in cond

    def test_the_success_report_does_not_claim_zero_on_failure(self):
        report = _by_name("Report containerd reclaim")
        cond = str(report.get("when"))
        assert "_reclaim_crictl.rc" in cond and "== 0" in cond, (
            "a failed prune must not print '0 image(s) removed'"
        )
