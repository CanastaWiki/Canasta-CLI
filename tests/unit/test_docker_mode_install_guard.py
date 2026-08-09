"""Docker mode cannot run a *local* `canasta install`.

`canasta install` provisions host-level software (k3s as a systemd unit,
the docker engine package, etc.), which cannot run from inside the
canasta-ansible container: it has no systemd and no privilege over the
host package manager. A *remote* install is fine — the playbook runs
over SSH on the target named by -H/--host, or resolved from -i/--id via
the registry, where systemd and root exist.

The guard lives in canasta.py rather than the canasta-docker wrapper
because only argparse has already parsed the target: it accepts the
attached short forms (-Hprod1.example.com, -imysite) a bash scan of "$@"
would have to re-parse, and pre-command flags ('canasta -v install
docker') that never put 'install' in $1. The -i lookup also reads the
real registry, where an instance created without --host has no 'host'
key at all — which means local, not "registered remotely".
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

import canasta  # noqa: E402

CANASTA_PY = os.path.join(os.path.dirname(os.path.abspath(canasta.__file__)),
                          "canasta.py")


# Temp config dirs created under /tmp (pytest's tmp_path lives under a
# longer path the wrapper's AF_UNIX limits dislike elsewhere in the
# suite, so these tests use /tmp directly). Registered by the helpers,
# emptied after each test so runs don't accumulate /tmp/ig-* dirs.
_tmp_config_dirs = []


@pytest.fixture(autouse=True)
def _cleanup_tmp_config_dirs():
    yield
    while _tmp_config_dirs:
        shutil.rmtree(_tmp_config_dirs.pop(), ignore_errors=True)


def _tmp_config_dir():
    d = tempfile.mkdtemp(prefix="ig-", dir="/tmp")
    _tmp_config_dirs.append(d)
    return d


def _args(**kw):
    kw.setdefault("host", None)
    kw.setdefault("id", None)
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
        canasta.check_docker_mode_install_target(args)
    return e.value.code


class TestItRefusesALocalInstall:
    def test_no_target_is_refused(self, registry):
        registry({})
        assert _refuses(_args()) == 1

    def test_the_message_says_why_and_what_to_do(self, registry, capsys):
        registry({})
        _refuses(_args())
        err = capsys.readouterr().err
        assert "not supported in canasta-docker mode" in err
        assert "get.canasta.wiki" in err
        assert "--native" in err
        assert "canasta install <package> -i <instance-id>" in err

    def test_host_localhost_is_refused(self, registry):
        registry({})
        assert _refuses(_args(host="localhost")) == 1

    def test_host_127_0_0_1_is_refused(self, registry):
        registry({})
        assert _refuses(_args(host="127.0.0.1")) == 1

    def test_host_localhost_wins_over_a_remote_id(self, registry):
        # --host wins over --id if both are given; a local --host is local
        # no matter what the registry says.
        registry({"rem1": {"id": "rem1", "host": "nichework.com"}})
        assert _refuses(_args(host="localhost", id="rem1")) == 1

    def test_an_instance_without_a_host_key_is_local(self, registry):
        # `canasta create` writes no 'host' key for a locally created
        # instance (instance_to_dict emits it only when truthy), so its
        # absence means local — the bug that reported such an instance
        # as "not registered".
        local = {"id": "loc1", "path": "/srv/loc1", "orchestrator": "compose"}
        registry({"loc1": local})
        assert _refuses(_args(id="loc1")) == 1

    def test_an_instance_with_empty_host_is_local(self, registry):
        registry({"loc1": {"id": "loc1", "host": ""}})
        assert _refuses(_args(id="loc1")) == 1

    def test_an_instance_with_localhost_host_is_local(self, registry):
        registry({"loc1": {"id": "loc1", "host": "localhost"}})
        assert _refuses(_args(id="loc1")) == 1

    def test_an_unknown_id_is_not_registered(self, registry, capsys):
        # 'not registered' is reserved for an ID absent from the registry;
        # a present ID with a missing/local host is just a local install.
        registry({"pod1": {"id": "pod1", "host": "localhost"}})
        with pytest.raises(SystemExit) as e:
            canasta.check_docker_mode_install_target(_args(id="nosuch"))
        assert e.value.code == 1
        err = capsys.readouterr().err
        assert "'nosuch' is not registered" in err
        assert "canasta create" in err
        assert "canasta host add" in err


class TestItLetsARemoteInstallThrough:
    def _ok(self, args):
        assert canasta.check_docker_mode_install_target(args) is None

    def test_a_remote_host_is_allowed(self, registry):
        registry({})
        self._ok(_args(host="prod1.example.com"))

    def test_a_remote_host_with_ssh_user_is_allowed(self, registry):
        registry({})
        self._ok(_args(host="cicalese@example.com"))

    def test_an_instance_registered_on_a_remote_host_is_allowed(self, registry):
        remote = {"id": "rem1", "host": "nichework.com"}
        registry({"rem1": remote})
        self._ok(_args(id="rem1"))

    def test_a_remote_host_wins_over_a_local_id(self, registry):
        # --host wins over --id if both are given.
        registry({"loc1": {"id": "loc1", "host": "localhost"}})
        self._ok(_args(host="prod1.example.com", id="loc1"))

    def test_native_mode_is_never_blocked(self, registry):
        # Native mode runs the install on the host, where systemd and the
        # package manager exist.
        registry({}, mode="native")
        self._ok(_args())


def _parse(argv):
    """Parse argv the way main() does, returning the args object.

    Mirrors the pre/post command split (so a pre-command -v lands on the
    global parser) and then the full subparser parse, which is where
    attached short options (-Hprod1, -imysite) are normalized to their
    long-form values. This is the shape the guard actually sees.
    """
    data = canasta.load_definitions()
    parser = canasta.build_parser(data)
    raw_args = list(argv)
    pre_cmd, post_cmd = [], []
    found_cmd = False
    cmd_names = {c["name"].split("_")[0] for c in data["commands"]}
    for arg in raw_args:
        if found_cmd:
            post_cmd.append(arg)
        elif not arg.startswith("-") and arg in cmd_names:
            found_cmd = True
            post_cmd.append(arg)
        else:
            pre_cmd.append(arg)
    global_parser = argparse.ArgumentParser(add_help=False)
    global_parser.add_argument("--verbose", "-v", action="store_true",
                               default=False)
    _, pre_remaining = global_parser.parse_known_args(pre_cmd)
    return parser.parse_args(pre_remaining + post_cmd)


class TestThroughArgparse:
    """Drive the guard with what argparse actually produces.

    These prove the two parsing bugs that motivated moving the decision
    out of the wrapper: attached short-option values (-Hprod1, -imysite)
    and flags that precede the command ('canasta -v install docker').
    """

    def _local_registry(self, instances):
        tmp = _tmp_config_dir()
        with open(os.path.join(tmp, "conf.json"), "w") as f:
            json.dump({"Instances": instances}, f)
        return tmp

    def _guard_for(self, monkeypatch, argv, instances=None):
        """Parse argv and run the guard against a seeded registry."""
        config_dir = self._local_registry(instances or {})
        args = _parse(argv)
        monkeypatch.setenv("CANASTA_RUN_MODE", "docker")
        monkeypatch.setattr(canasta, "get_config_file_path",
                            lambda: os.path.join(config_dir, "conf.json"))
        monkeypatch.setattr(canasta, "get_config_dir", lambda: config_dir)
        return canasta.check_docker_mode_install_target(args)

    def test_pre_command_verbose_install_docker_is_refused(self, monkeypatch):
        # Bug: '$1 == install' misses 'canasta -v install docker', so the
        # install playbook ran on the controller from inside the
        # container. argparse puts -v on the global parser and the guard
        # still sees command install with no target.
        with pytest.raises(SystemExit) as e:
            self._guard_for(monkeypatch, ["-v", "install", "docker"])
        assert e.value.code == 1

    def test_attached_short_host_form_is_refused(self, monkeypatch):
        # Bug: the wrapper scanned for '-H|--host' and '-H=*' but not the
        # attached '-Hlocalhost' argparse accepts.
        with pytest.raises(SystemExit) as e:
            self._guard_for(monkeypatch, ["install", "podman", "-Hlocalhost"])
        assert e.value.code == 1

    def test_attached_short_id_form_resolves_through_the_registry(
            self, monkeypatch):
        # Bug: '-imysite' was invisible to the wrapper's separated -i
        # scan, so a local instance's missing host key slipped through.
        local = {"id": "mysite", "path": "/srv/mysite",
                 "orchestrator": "compose"}
        with pytest.raises(SystemExit) as e:
            self._guard_for(monkeypatch, ["install", "sops", "-imysite"],
                            {"mysite": local})
        assert e.value.code == 1

    def test_attached_short_id_form_for_a_remote_instance_is_allowed(
            self, monkeypatch):
        remote = {"id": "rem1", "path": "/srv/rem1", "host": "nichework.com"}
        assert self._guard_for(monkeypatch, ["install", "git-crypt", "-irem1"],
                               {"rem1": remote}) is None

    def test_attached_short_host_form_for_a_remote_host_is_allowed(
            self, monkeypatch):
        assert self._guard_for(
            monkeypatch, ["install", "podman", "-Hprod1.example.com"]) is None


class TestThroughRealMain:
    """Run canasta.py end to end so main()'s wiring is exercised.

    These stop at the guard (exit 1 before any ansible/network work), so
    they are safe and deterministic under the test interpreter.
    """

    def _run(self, argv, instances=None):
        config_dir = _tmp_config_dir()
        with open(os.path.join(config_dir, "conf.json"), "w") as f:
            json.dump({"Instances": instances or {}}, f)
        env = os.environ.copy()
        env.update({
            "CANASTA_RUN_MODE": "docker",
            "CANASTA_CONFIG_DIR": config_dir,
        })
        return subprocess.run(
            [sys.executable, CANASTA_PY] + list(argv),
            env=env, cwd="/tmp", capture_output=True, text=True,
        )

    def test_pre_command_verbose_install_docker_is_refused(self):
        result = self._run(["-v", "install", "docker"])
        assert result.returncode == 1
        assert "not supported in canasta-docker mode" in result.stderr

    def test_attached_host_form_is_refused(self):
        result = self._run(["install", "podman", "-Hlocalhost"])
        assert result.returncode == 1
        assert "not supported in canasta-docker mode" in result.stderr

    def test_attached_id_form_resolves_through_the_registry(self):
        local = {"id": "mysite", "path": "/srv/mysite",
                 "orchestrator": "compose"}
        result = self._run(["install", "sops", "-imysite"],
                           instances={"mysite": local})
        assert result.returncode == 1
        assert "not supported in canasta-docker mode" in result.stderr

    def test_unknown_id_through_the_registry(self):
        result = self._run(["install", "sops", "-i", "nosuch"])
        assert result.returncode == 1
        assert "'nosuch' is not registered" in result.stderr
