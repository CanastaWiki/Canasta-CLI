"""An instance with no recorded runtime must be asked, not assumed.

composeCommand/inspectCommand became registry fields after instances
were already registered, so every instance created before them reads
back as Docker. _upgrade_single.yml takes the registry at its word, and
on a Podman-only host the whole upgrade runs `docker compose`:

    Pulling Canasta container images...
    Error: pull Compose images failed (rc=127): /bin/sh: 1: docker: not found

with `canasta doctor` on the same host reporting Docker MISSING and
Podman OK. The probe has to run on the instance's host (the controller's
PATH says nothing about it) and the answer has to reach the registry, or
`canasta list` and `canasta start` keep resolving that instance to
Docker afterwards.
"""

import json
import os

import yaml

import canasta_registry
from mock_ansible import run_module_with_params

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
SINGLE = os.path.join(REPO_ROOT, "playbooks", "_upgrade_single.yml")
DETECT = os.path.join(
    REPO_ROOT, "roles", "common", "tasks", "detect_runtime.yml")


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _tasks(path):
    out = []

    def walk(node):
        if isinstance(node, dict):
            out.append(node)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for i in node:
                walk(i)

    walk(_load(path))
    return out


def _named(path, needle):
    return next(
        (t for t in _tasks(path)
         if needle.lower() in str(t.get("name", "")).lower()), None)


def _params(**kw):
    params = {
        "state": "query", "id": None, "path": None,
        "orchestrator": "compose", "dev_mode": False,
        "managed_cluster": False, "registry": None, "kind_cluster": None,
        "build_from": None, "build_args": None, "host": None,
        "docker_host": None, "compose_command": None,
        "inspect_command": None, "filter_host": None,
        "setting_key": None, "setting_value": None, "config_dir": None,
    }
    params.update(kw)
    return params


def _legacy_registry(tmp_dir):
    """An instance recorded before the runtime fields existed."""
    data = {"Instances": {"legacy": {
        "id": "legacy",
        "path": "/srv/canasta/legacy",
        "orchestrator": "compose",
        "host": "host1.example.com",
        "devMode": True,
        "buildFrom": "/src",
        "dockerHost": "unix:///run/user/1001/podman/podman.sock",
    }}}
    with open(os.path.join(tmp_dir, "conf.json"), "w") as f:
        json.dump(data, f)
    return data["Instances"]["legacy"]


def _read_back(tmp_dir):
    with open(os.path.join(tmp_dir, "conf.json")) as f:
        return json.load(f)["Instances"]["legacy"]


class TestUpgradeProbesWhenTheRegistryIsSilent:
    def test_the_detection_is_included(self):
        task = _named(SINGLE, "Detect the runtime")
        assert task, (
            "_upgrade_single.yml takes the registry's silence as Docker, "
            "so an instance registered before composeCommand existed runs "
            "the whole upgrade with `docker compose`"
        )
        assert task["ansible.builtin.include_tasks"].endswith(
            "roles/common/tasks/detect_runtime.yml")

    def test_it_only_runs_when_nothing_is_recorded(self):
        conds = _named(SINGLE, "Detect the runtime")["when"]
        assert any("composeCommand is not defined" in str(c) for c in conds), (
            "the probe runs even for instances that record their runtime, "
            "spending two round trips per upgrade to relearn it"
        )
        assert any("compose" in str(c) and "orchestrator" in str(c)
                   for c in conds), (
            "Kubernetes instances have no compose runtime to probe"
        )

    def test_it_runs_after_the_connection_switch(self):
        # The controller's PATH is not evidence about the instance's host.
        names = [str(t.get("name", "")) for t in _tasks(SINGLE)]
        assert (names.index("Switch connection to instance host")
                < names.index(
                    "Detect the runtime for an instance that has none "
                    "recorded"))

    def test_the_facts_are_reset_before_the_probe(self):
        # set_fact persists across loop iterations, so a podman instance
        # detected in one pass must not leak into the next instance.
        names = [str(t.get("name", "")) for t in _tasks(SINGLE)]
        assert (names.index("Set instance facts")
                < names.index(
                    "Detect the runtime for an instance that has none "
                    "recorded"))


class TestTheProbeAsksTheHost:
    def test_it_probes_both_runtimes(self):
        cmds = [t["ansible.builtin.command"]["cmd"] for t in _tasks(DETECT)
                if "ansible.builtin.command" in t]
        assert "docker info" in cmds
        assert "podman info" in cmds

    def test_a_missing_binary_does_not_abort_the_upgrade(self):
        # `docker info` where docker is absent leaves no rc at all.
        for t in _tasks(DETECT):
            if "ansible.builtin.command" not in t:
                continue
            assert t["failed_when"] is False
            assert t["changed_when"] is False
        block = _named(DETECT, "Use Podman when Docker is absent")
        assert all("default(1)" in str(c) for c in block["when"]), (
            "the conditions read .rc directly, which is undefined when the "
            "binary is missing — the case this exists to handle"
        )

    def test_podman_wins_only_when_docker_is_unavailable(self):
        conds = [str(c) for c
                 in _named(DETECT, "Use Podman when Docker is absent")["when"]]
        assert any("_dr_docker" in c and "!= 0" in c for c in conds)
        assert any("_dr_podman" in c and "== 0" in c for c in conds)

    def test_the_result_is_written_back_to_the_registry(self):
        task = _named(DETECT, "Record the detected runtime")
        assert task, (
            "without the write-back the probe repeats every upgrade, and "
            "list/start/exec keep resolving the instance to Docker"
        )
        assert task["canasta_registry"]["state"] == "set_runtime"
        assert task["canasta_registry"]["compose_command"] == "podman-compose"
        assert task["canasta_registry"]["inspect_command"] == "podman"

    def test_the_registry_write_runs_on_the_controller(self):
        task = _named(DETECT, "Record the detected runtime")
        assert task["delegate_to"] == "localhost"
        assert task["vars"]["ansible_connection"] == "local"


class TestSetRuntimeKeepsTheRestOfTheRecord:
    def test_it_records_the_runtime(self, tmp_dir):
        _legacy_registry(tmp_dir)
        _, failed, _ = run_module_with_params(canasta_registry, _params(
            state="set_runtime", id="legacy",
            compose_command="podman-compose", inspect_command="podman",
            config_dir=tmp_dir))
        assert not failed
        entry = _read_back(tmp_dir)
        assert entry["composeCommand"] == "podman-compose"
        assert entry["inspectCommand"] == "podman"

    def test_it_preserves_fields_it_was_not_given(self, tmp_dir):
        # state=present rebuilds the record from module params, so
        # backfilling through it would drop all of these.
        before = _legacy_registry(tmp_dir)
        run_module_with_params(canasta_registry, _params(
            state="set_runtime", id="legacy",
            compose_command="podman-compose", inspect_command="podman",
            config_dir=tmp_dir))
        entry = _read_back(tmp_dir)
        for key, value in before.items():
            assert entry[key] == value, "set_runtime dropped %s" % key

    def test_it_is_idempotent(self, tmp_dir):
        _legacy_registry(tmp_dir)
        for _ in range(2):
            result, failed, _ = run_module_with_params(
                canasta_registry, _params(
                    state="set_runtime", id="legacy",
                    compose_command="podman-compose",
                    inspect_command="podman", config_dir=tmp_dir))
            assert not failed
        assert result["changed"] is False

    def test_it_refuses_an_unregistered_instance(self, tmp_dir):
        _legacy_registry(tmp_dir)
        _, failed, msg = run_module_with_params(canasta_registry, _params(
            state="set_runtime", id="ghost",
            compose_command="podman-compose", config_dir=tmp_dir))
        assert failed
        assert "not found" in msg

    def test_it_requires_an_id(self, tmp_dir):
        _legacy_registry(tmp_dir)
        _, failed, msg = run_module_with_params(canasta_registry, _params(
            state="set_runtime", compose_command="podman-compose",
            config_dir=tmp_dir))
        assert failed
        assert "id is required" in msg
