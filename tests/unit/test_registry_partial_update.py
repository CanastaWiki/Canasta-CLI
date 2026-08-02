"""state=present replaces a registry record; state=update merges into it.

`present` rebuilds the record from the module params and assigns it over
the old one, so every field the caller does not repeat is dropped. That
reads like an upsert and behaves like a replace, and each caller that
wanted to change one field had to know it must re-send all the others.
`canasta devmode enable|disable` did not, and quietly erased dockerHost
until that was patched in — then composeCommand, inspectCommand and
buildArgs, which arrived later and nobody went back to add.

Losing composeCommand puts a Podman instance back on the `docker
compose` default, which is the failure in #1370:

    Error: pull Compose images failed (rc=127): /bin/sh: 1: docker: not found

state=update carries only the params actually supplied, so a caller
naming one field cannot disturb the rest. `present` keeps its
create-or-replace meaning for `create`, which owns the whole record.
"""

import json
import os

import canasta_registry
from mock_ansible import run_module_with_params

FULL = {
    "id": "demo",
    "path": "/srv/canasta/demo",
    "orchestrator": "compose",
    "host": "host1.example.com",
    "devMode": True,
    "managedCluster": True,
    "registry": "localhost:5000",
    "kindCluster": "kind-demo",
    "buildFrom": "/src",
    "buildArgs": ["FOO=1", "BAR=2"],
    "dockerHost": "unix:///run/user/1001/podman/podman.sock",
    "composeCommand": "podman-compose",
    "inspectCommand": "podman",
}


def _params(**kw):
    params = {
        "state": "query", "id": None, "path": None, "orchestrator": None,
        "dev_mode": None, "managed_cluster": None, "registry": None,
        "kind_cluster": None, "build_from": None, "build_args": None,
        "host": None, "docker_host": None, "compose_command": None,
        "inspect_command": None, "filter_host": None, "setting_key": None,
        "setting_value": None, "config_dir": None,
    }
    params.update(kw)
    return params


def _seed(tmp_dir, record=None):
    record = dict(FULL if record is None else record)
    with open(os.path.join(tmp_dir, "conf.json"), "w") as f:
        json.dump({"Instances": {record["id"]: record}}, f)
    return record


def _read_back(tmp_dir, inst_id="demo"):
    with open(os.path.join(tmp_dir, "conf.json")) as f:
        return json.load(f)["Instances"][inst_id]


def _update(tmp_dir, **kw):
    kw.setdefault("id", "demo")
    return run_module_with_params(canasta_registry, _params(
        state="update", config_dir=tmp_dir, **kw))


class TestUpdateTouchesOnlyWhatItIsGiven:
    def test_it_writes_the_field_it_is_given(self, tmp_dir):
        _seed(tmp_dir)
        _, failed, _ = _update(tmp_dir, compose_command="podman-compose",
                               inspect_command="podman")
        assert not failed
        entry = _read_back(tmp_dir)
        assert entry["composeCommand"] == "podman-compose"
        assert entry["inspectCommand"] == "podman"

    def test_it_leaves_every_other_field_alone(self, tmp_dir):
        before = _seed(tmp_dir)
        _update(tmp_dir, dev_mode=False)
        entry = _read_back(tmp_dir)
        for key, value in before.items():
            if key == "devMode":
                continue
            assert entry[key] == value, "update dropped %s" % key

    def test_a_false_boolean_is_a_value_not_a_silence(self, tmp_dir):
        # The whole bug: with an argument default, "not supplied" and
        # "supplied false" are the same value, so every unmentioned flag
        # looks like an explicit false.
        _seed(tmp_dir)
        _update(tmp_dir, dev_mode=False)
        entry = _read_back(tmp_dir)
        assert "devMode" not in entry
        assert entry["managedCluster"] is True, (
            "managed_cluster defaulted to false and was written back over "
            "a record that had it set"
        )

    def test_a_true_boolean_is_recorded(self, tmp_dir):
        _seed(tmp_dir, dict(FULL, devMode=False))
        _update(tmp_dir, dev_mode=True)
        assert _read_back(tmp_dir)["devMode"] is True

    def test_an_empty_string_clears_the_field(self, tmp_dir):
        # Matches instance_to_dict: the stored format carries a key only
        # while it holds something.
        _seed(tmp_dir)
        _update(tmp_dir, host="")
        assert "host" not in _read_back(tmp_dir)

    def test_it_is_idempotent(self, tmp_dir):
        _seed(tmp_dir)
        result, failed, _ = _update(tmp_dir, compose_command="podman-compose")
        assert not failed
        assert result["changed"] is False, (
            "an update that changes nothing reports changed, so every "
            "playbook run using it looks like it did something"
        )

    def test_it_reports_a_real_change(self, tmp_dir):
        _seed(tmp_dir)
        result, _, _ = _update(tmp_dir, compose_command="docker compose")
        assert result["changed"] is True


class TestUpdateRefusesWhatItCannotMerge:
    def test_it_requires_an_id(self, tmp_dir):
        _seed(tmp_dir)
        _, failed, msg = run_module_with_params(canasta_registry, _params(
            state="update", dev_mode=True, config_dir=tmp_dir))
        assert failed
        assert "id is required" in msg

    def test_it_refuses_an_unregistered_instance(self, tmp_dir):
        # An update has nothing to merge onto, and creating a partial
        # record here would produce an instance with no path.
        _seed(tmp_dir)
        _, failed, msg = _update(tmp_dir, id="ghost", dev_mode=True)
        assert failed
        assert "not found" in msg

    def test_a_refused_update_leaves_the_registry_untouched(self, tmp_dir):
        before = _seed(tmp_dir)
        _update(tmp_dir, id="ghost", dev_mode=True)
        assert _read_back(tmp_dir) == before


class TestPresentStillReplaces:
    """create owns the whole record and relies on this."""

    def test_it_preserves_fields_it_was_not_given(self, tmp_dir):
        _seed(tmp_dir)
        _, failed, _ = run_module_with_params(canasta_registry, _params(
            state="present", id="demo", path="/srv/canasta/demo",
            orchestrator="compose", config_dir=tmp_dir))
        assert not failed
        entry = _read_back(tmp_dir)
        assert entry["composeCommand"] == "podman-compose"
        assert entry["inspectCommand"] == "podman"

    def test_the_orchestrator_default_survived_losing_its_argspec_default(
            self, tmp_dir):
        # The argument default moved into the present branch so update
        # could tell "not supplied" from "supplied"; present must still
        # record compose when nothing is passed.
        _, failed, _ = run_module_with_params(canasta_registry, _params(
            state="present", id="fresh", path="/srv/canasta/fresh",
            config_dir=tmp_dir))
        assert not failed
        assert _read_back(tmp_dir, "fresh")["orchestrator"] == "compose"

    def test_unset_booleans_are_still_omitted(self, tmp_dir):
        run_module_with_params(canasta_registry, _params(
            state="present", id="fresh", path="/srv/canasta/fresh",
            config_dir=tmp_dir))
        entry = _read_back(tmp_dir, "fresh")
        assert "devMode" not in entry
        assert "managedCluster" not in entry


class TestEveryStoredFieldCanBeUpdated:
    def test_the_map_covers_the_stored_format(self):
        # instance_to_dict defines what a record may hold; a field
        # missing from UPDATABLE_FIELDS is one no caller can change
        # without falling back to a whole-record replace.
        stored = set(canasta_registry.instance_to_dict(FULL))
        updatable = {key for key, _param in canasta_registry.UPDATABLE_FIELDS}
        assert stored - updatable == {"id", "path"}
