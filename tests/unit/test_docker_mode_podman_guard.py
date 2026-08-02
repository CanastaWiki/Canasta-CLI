"""Docker mode cannot drive a Podman instance local to the controller.

The CLI runs inside the canasta-ansible container, which ships docker
and `docker compose` but no podman-compose. Without a guard, an upgrade
gets as far as rewriting the stack files and then dies:

    Writing Compose stack files...
    Pulling Canasta container images...
    Error: pull Compose images failed (rc=127): /bin/sh: 1: podman-compose: not found

Remote instances are deliberately *not* blocked: their compose commands
run on the target over SSH, where podman-compose exists. That path was
verified working end to end (status and restart) against a remote
Podman instance, so blocking it would break the topology that matters
most for Podman.

Installing podman-compose on the host does not change any of this — the
command runs inside the container, and only the socket, the registry,
$HOME and the working directory are mounted, not host binaries.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

import canasta  # noqa: E402

PODMAN_LOCAL = {
    "id": "pod1", "path": "/srv/pod1", "orchestrator": "compose",
    "host": "localhost",
    "dockerHost": "unix:///run/user/1000/podman/podman.sock",
    "composeCommand": "podman-compose", "inspectCommand": "podman",
}
DOCKER_LOCAL = {
    "id": "doc1", "path": "/srv/doc1", "orchestrator": "compose",
    "host": "localhost", "dockerHost": "unix:///var/run/docker.sock",
    "composeCommand": "docker compose", "inspectCommand": "docker",
}
PODMAN_REMOTE = dict(PODMAN_LOCAL, id="rem1", path="/srv/rem1",
                     host="cicalese@example.com")


def _args(**kw):
    kw.setdefault("id", None)
    kw.setdefault("path", None)
    return type("Args", (), kw)()


@pytest.fixture
def registry(monkeypatch, tmp_path):
    """Point the guard at a registry we control, in docker mode."""
    def _install(instances, mode="docker"):
        conf = tmp_path / "conf.json"
        conf.write_text("{}")
        monkeypatch.setenv("CANASTA_RUN_MODE", mode)
        monkeypatch.setattr(canasta, "get_config_file_path", lambda: str(conf))
        monkeypatch.setattr(canasta, "get_config_dir", lambda: str(tmp_path))
        monkeypatch.setattr(canasta.canasta_config, "read_config",
                            lambda d: {"Instances": instances})
    return _install


def _refuses(args):
    with pytest.raises(SystemExit) as e:
        canasta.check_docker_mode_can_reach_runtime(args)
    return e.value.code


class TestItRefusesALocalPodmanInstance:
    def test_named_instance_is_refused(self, registry):
        registry({"pod1": PODMAN_LOCAL})
        assert _refuses(_args(id="pod1")) == 1

    def test_the_message_says_why_and_what_to_do(self, registry, capsys):
        registry({"pod1": PODMAN_LOCAL})
        _refuses(_args(id="pod1"))
        err = capsys.readouterr().err
        assert "pod1" in err
        assert "podman-compose" in err
        assert "get.canasta.wiki" in err
        assert "--native" in err

    def test_it_says_installing_on_the_host_will_not_help(self, registry,
                                                          capsys):
        # Otherwise the obvious next move is `brew install podman-compose`,
        # which cannot reach inside the container.
        registry({"pod1": PODMAN_LOCAL})
        _refuses(_args(id="pod1"))
        assert "does not reach it" in capsys.readouterr().err

    def test_an_untargeted_command_checks_every_instance(self, registry):
        # `upgrade` takes no -i and acts on all of them, so one bad record
        # must stop it before it half-finishes the others.
        registry({"doc1": DOCKER_LOCAL, "pod1": PODMAN_LOCAL})
        assert _refuses(_args()) == 1

    def test_it_matches_by_path_too(self, registry):
        registry({"pod1": PODMAN_LOCAL})
        assert _refuses(_args(path="/srv/pod1")) == 1

    def test_a_legacy_record_with_only_a_podman_socket_is_caught(self,
                                                                registry):
        # Neither command recorded; the runtime is inferred from the
        # socket, so the compose call still needs podman-compose.
        legacy = {"id": "leg", "path": "/srv/leg", "orchestrator": "compose",
                  "host": "localhost",
                  "dockerHost": "unix:///run/user/1000/podman/podman.sock"}
        registry({"leg": legacy})
        assert _refuses(_args(id="leg")) == 1


class TestItDoesNotRefuseAnythingElse:
    def _ok(self, args):
        assert canasta.check_docker_mode_can_reach_runtime(args) is None

    def test_a_remote_podman_instance_is_allowed(self, registry):
        # Verified working end to end; compose runs on the target.
        registry({"rem1": PODMAN_REMOTE})
        self._ok(_args(id="rem1"))

    def test_a_local_docker_instance_is_allowed(self, registry):
        registry({"doc1": DOCKER_LOCAL})
        self._ok(_args(id="doc1"))

    def test_native_mode_is_never_blocked(self, registry):
        # Native mode runs podman-compose from the host, where it exists.
        registry({"pod1": PODMAN_LOCAL}, mode="native")
        self._ok(_args(id="pod1"))

    def test_a_kubernetes_instance_is_not_compose_and_is_allowed(self,
                                                                 registry):
        k8s = dict(PODMAN_LOCAL, id="k1", orchestrator="kubernetes")
        registry({"k1": k8s})
        self._ok(_args(id="k1"))

    def test_targeting_a_docker_instance_ignores_an_unrelated_podman_one(
            self, registry):
        # Scoped commands must not be blocked by a record they never touch.
        registry({"doc1": DOCKER_LOCAL, "pod1": PODMAN_LOCAL})
        self._ok(_args(id="doc1"))

    def test_an_unknown_id_is_left_to_the_normal_error_path(self, registry):
        registry({"pod1": PODMAN_LOCAL})
        self._ok(_args(id="nosuch"))

    def test_no_registry_is_not_an_error(self, monkeypatch):
        monkeypatch.setenv("CANASTA_RUN_MODE", "docker")
        monkeypatch.setattr(canasta, "get_config_file_path",
                            lambda: "/nonexistent/conf.json")
        self._ok(_args(id="pod1"))
