"""An instance's .env must be read on the host that has it.

`inst["path"]` points at the instance directory on *its own host*. For a
remote instance that path does not exist on the controller, so reading
it locally returns nothing — and nothing is indistinguishable from "the
key is unset".

That is how remote podman instances lost their compose profiles:
_compose_profile_args read COMPOSE_PROFILES from the controller, got
None, returned no --profile flags, and every profiled service (the
database included) was silently omitted from `up`.
"""

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
sys.path.insert(0, REPO_ROOT)

from direct_commands import _helpers  # noqa: E402


PODMAN = {"composeCommand": "podman-compose", "inspectCommand": "podman"}


def _remote(**kw):
    inst = {"path": "/home/op/canasta/site", "host": "someremote"}
    inst.update(kw)
    return inst


def _local(tmp_path, body, **kw):
    (tmp_path / ".env").write_text(body)
    inst = {"path": str(tmp_path), "host": "localhost"}
    inst.update(kw)
    return inst


class TestLocalInstancesStillReadLocally:
    def test_reads_the_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_helpers, "_ssh_run", _fail_if_called)
        inst = _local(tmp_path, "COMPOSE_PROFILES=varnish,internal-db\n", **PODMAN)
        assert _helpers._compose_profile_args(inst) == [
            "--profile", "varnish", "--profile", "internal-db"]


def _fail_if_called(*a, **kw):
    raise AssertionError("a local instance must not be read over ssh")


class TestRemoteInstancesReadOverSsh:
    def test_profiles_come_from_the_remote_env(self, monkeypatch):
        calls = []

        def fake_ssh(host, cmd):
            calls.append((host, cmd))
            return 0, "varnish,internal-db\n"

        monkeypatch.setattr(_helpers, "_ssh_run", fake_ssh)
        _helpers._REMOTE_ENV_CACHE.clear()
        args = _helpers._compose_profile_args(_remote(**PODMAN))

        assert args == ["--profile", "varnish", "--profile", "internal-db"], (
            "profiles were not read from the instance's own host, so "
            "profiled services would be omitted from up/down"
        )
        assert calls and calls[0][0] == "someremote"
        assert "/home/op/canasta/site/.env" in calls[0][1]

    def test_the_resolvers_do_not_reach_over_ssh(self, monkeypatch):
        # _resolve_compose_cmd runs on every compose invocation, so it
        # must stay free of I/O. Remote instances carry the runtime in
        # the registry; the .env fallback is local-only by design.
        monkeypatch.setattr(_helpers, "_ssh_run", _fail_if_called)
        assert _helpers._resolve_compose_cmd(_remote()) == ["docker", "compose"]
        assert _helpers._resolve_inspect_cmd(_remote()) == "docker"

    def test_the_registry_still_wins_for_remote(self, monkeypatch):
        monkeypatch.setattr(_helpers, "_ssh_run", _fail_if_called)
        assert _helpers._resolve_compose_cmd(_remote(**PODMAN)) == [
            "podman-compose"]

    def test_the_read_is_cached(self, monkeypatch):
        calls = []

        def counting_ssh(host, cmd):
            calls.append(cmd)
            return 0, "varnish\n"

        monkeypatch.setattr(_helpers, "_ssh_run", counting_ssh)
        _helpers._REMOTE_ENV_CACHE.clear()
        inst = _remote(**PODMAN)
        for _ in range(4):
            _helpers._compose_profile_args(inst)
        assert len(calls) == 1, (
            "one ssh round-trip per compose call would be felt in "
            "`canasta list` across several remote instances"
        )

    def test_a_failed_read_is_not_a_value(self, monkeypatch):
        monkeypatch.setattr(_helpers, "_ssh_run", lambda h, c: (255, ""))
        _helpers._REMOTE_ENV_CACHE.clear()
        assert _helpers._compose_profile_args(_remote(**PODMAN)) == []

    def test_quoted_values_are_unwrapped(self, monkeypatch):
        monkeypatch.setattr(
            _helpers, "_ssh_run", lambda h, c: (0, '"varnish"\n'))
        _helpers._REMOTE_ENV_CACHE.clear()
        assert _helpers._compose_profile_args(_remote(**PODMAN)) == [
            "--profile", "varnish"]


class TestDockerInstancesAreUnaffected:
    def test_no_profile_flags_for_docker(self, monkeypatch):
        monkeypatch.setattr(_helpers, "_ssh_run", _fail_if_called)
        # Not podman: the function returns before any .env read.
        assert _helpers._compose_profile_args(
            _remote(composeCommand="docker compose")) == []
