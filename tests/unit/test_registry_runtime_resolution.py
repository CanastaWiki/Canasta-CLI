"""The registry is where an instance's runtime is recorded — read it.

build_ansible_args documented "a registry entry (composeCommand /
inspectCommand) wins, then the instance's .env", but only ever read the
.env. create records the probed runtime in the registry and does not
write it to .env, so no extra-var was set and every Ansible task fell
back to the play-scope default of `docker compose`:

    $ canasta config set -i podtest RESTIC_REPOSITORY=...
    Cannot connect to the Docker daemon at unix:///var/run/docker.sock.
    (cmd: docker compose -f docker-compose.yml down)

against a healthy rootless-Podman instance. Two paths had already been
patched one at a time for the same reason — _upgrade_single.yml and
roles/delete/tasks/main.yml — while 16 other task files using
compose_command still inherited the default.

The registry lives on the controller, so unlike the .env it reads the
same for a remote instance.
"""

import json
import os
import sys

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
sys.path.insert(0, REPO_ROOT)

import canasta  # noqa: E402

PODMAN = {"composeCommand": "podman-compose", "inspectCommand": "podman"}


def _registry(tmp_path, monkeypatch, **fields):
    cfg = tmp_path / "cfg"
    inst = tmp_path / "inst"
    cfg.mkdir()
    inst.mkdir()
    record = {"path": str(inst), "orchestrator": "compose", "host": "localhost"}
    record.update(fields)
    (cfg / "conf.json").write_text(json.dumps({"Instances": {"demo": record}}))
    monkeypatch.setenv("CANASTA_CONFIG_DIR", str(cfg))
    return inst


def _args(**kw):
    class _A:
        def __getattr__(self, name):
            return None
    a = _A()
    a.verbose = False
    for k, v in kw.items():
        setattr(a, k, v)
    return a


def _definitions():
    with open(os.path.join(REPO_ROOT, "meta", "command_definitions.yml")) as f:
        return yaml.safe_load(f)


def _extra_vars(argv):
    path = argv[argv.index("-e") + 1].lstrip("@")
    with open(path) as f:
        return json.load(f)


def _run(command="config_set", **kw):
    return _extra_vars(canasta.build_ansible_args(
        "ansible-playbook", command, _args(id="demo", **kw), _definitions()))


class TestTheRegistryIsRead:
    def test_compose_command_comes_from_the_registry(self, tmp_path,
                                                     monkeypatch):
        _registry(tmp_path, monkeypatch, **PODMAN)
        assert _run()["compose_command"] == "podman-compose", (
            "without this every Ansible task runs `docker compose` against "
            "a podman instance"
        )

    def test_inspect_command_too(self, tmp_path, monkeypatch):
        _registry(tmp_path, monkeypatch, **PODMAN)
        assert _run()["inspect_command"] == "podman"

    def test_it_applies_to_any_command(self, tmp_path, monkeypatch):
        # The point of fixing this centrally: playbooks that never read
        # the registry themselves still get the right runtime.
        _registry(tmp_path, monkeypatch, **PODMAN)
        for cmd in ("config_set", "reconcile", "backup_create"):
            assert _run(cmd)["compose_command"] == "podman-compose", (
                "%s still inherits the default runtime" % cmd
            )


class TestRemoteInstances:
    def test_the_registry_is_read_for_a_remote_instance(self, tmp_path,
                                                        monkeypatch):
        # The .env fallback is local-only by necessity — it sits on the
        # instance's host. The registry does not have that limitation.
        _registry(tmp_path, monkeypatch, host="prod1.example.com", **PODMAN)
        assert _run()["compose_command"] == "podman-compose"


class TestPrecedence:
    def test_the_registry_wins_over_env(self, tmp_path, monkeypatch):
        inst = _registry(tmp_path, monkeypatch, **PODMAN)
        (inst / ".env").write_text("compose_command=docker compose\n")
        assert _run()["compose_command"] == "podman-compose", (
            "the registry records what create actually probed; a stale "
            ".env must not override it"
        )

    def test_env_still_covers_older_instances(self, tmp_path, monkeypatch):
        # Instances created before the registry carried these fields.
        inst = _registry(tmp_path, monkeypatch)
        (inst / ".env").write_text("compose_command=podman-compose\n")
        assert _run()["compose_command"] == "podman-compose"

    def test_the_runtime_is_not_settable_from_the_cli(self):
        # Which is why the registry can be read unconditionally: there is
        # no operator-supplied value for it to trample. The guard in
        # build_ansible_args is defensive only.
        defs = _definitions()
        declared = [
            p["name"] for c in defs["commands"]
            for p in c.get("parameters", [])
            if p["name"] in ("compose_command", "inspect_command")
        ]
        assert declared == [], (
            "a CLI flag for the runtime would outrank the registry and the "
            "create_preflight probe both; resolution would need reordering"
        )


class TestNothingIsInventedWhenUnknown:
    def test_no_runtime_recorded_injects_nothing(self, tmp_path, monkeypatch):
        # The default belongs in vars/compose_runtime.yml, at a precedence
        # create_preflight.yml's probe can still override — an extra-var
        # here would silently outrank a probe that asked the target.
        _registry(tmp_path, monkeypatch)
        vars_ = _run()
        assert "compose_command" not in vars_
        assert "inspect_command" not in vars_

    def test_a_partial_record_only_sets_what_it_has(self, tmp_path,
                                                    monkeypatch):
        _registry(tmp_path, monkeypatch, composeCommand="podman-compose")
        vars_ = _run()
        assert vars_["compose_command"] == "podman-compose"
        assert "inspect_command" not in vars_
