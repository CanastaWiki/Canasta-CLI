"""Paths that name a file on the instance's host (path_kind: instance_host).

On an instance local to the controller they resolve against the working
directory, as before. On a remote instance, whose host comes from --host or
the registry, they reach the target unexpanded, and a relative value is
refused because it would depend on a working directory there.
"""

import json
import os
import sys

import pytest
import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
sys.path.insert(0, REPO_ROOT)

import canasta  # noqa: E402


@pytest.fixture
def registry(tmp_path, monkeypatch):
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "conf.json").write_text(json.dumps({
        "Instances": {
            "here": {"path": str(tmp_path / "here"),
                     "orchestrator": "compose", "host": "localhost"},
            "there": {"path": "/home/canasta/there",
                      "orchestrator": "compose",
                      "host": "admin@prod.example.com"},
        },
    }))
    monkeypatch.setenv("CANASTA_CONFIG_DIR", str(cfg))
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    return work


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


def _extra_vars(command, args):
    argv = canasta.build_ansible_args(
        "ansible-playbook", command, args, _definitions())
    with open(argv[argv.index("-e") + 1].lstrip("@")) as f:
        return json.load(f)


def _refused(command, args, capsys):
    with pytest.raises(SystemExit) as exc:
        canasta.build_ansible_args(
            "ansible-playbook", command, args, _definitions())
    assert exc.value.code == 1
    return capsys.readouterr().err


class TestLocalInstance:
    def test_export_relative_file_resolves_against_cwd(self, registry):
        ev = _extra_vars("export", _args(id="here", wiki="main",
                                         file="x.sql.gz"))
        assert ev["file"] == str(registry / "x.sql.gz")

    def test_export_tilde_expands_here(self, registry):
        ev = _extra_vars("export", _args(id="here", wiki="main",
                                         file="~/x.sql"))
        assert ev["file"] == os.path.expanduser("~/x.sql")

    def test_gitops_pull_relative_ssh_key_resolves_against_cwd(self, registry):
        ev = _extra_vars("gitops_pull", _args(id="here", ssh_key="keys/k"))
        assert ev["ssh_key"] == str(registry / "keys" / "k")

    def test_create_without_host_resolves_build_from_locally(self, registry):
        ev = _extra_vars("create", _args(id="new", wiki="main", path=".",
                                         build_from="src"))
        assert ev["build_from"] == str(registry / "src")

    def test_create_decides_from_host_flag_not_registry(self, registry):
        # 'there' is registered on a remote host, but create has --host,
        # so without it the build source is on this machine.
        ev = _extra_vars("create", _args(id="there", wiki="main", path=".",
                                         build_from="src"))
        assert ev["build_from"] == str(registry / "src")


class TestRemoteInstance:
    def test_export_tilde_reaches_target_unexpanded(self, registry):
        ev = _extra_vars("export", _args(id="there", wiki="main",
                                         file="~/x.sql.gz"))
        assert ev["file"] == "~/x.sql.gz"

    def test_export_absolute_passes_through(self, registry):
        ev = _extra_vars("export", _args(id="there", wiki="main",
                                         file="/srv/dumps/x.sql"))
        assert ev["file"] == "/srv/dumps/x.sql"

    def test_export_relative_file_refused(self, registry, capsys):
        err = _refused("export", _args(id="there", wiki="main",
                                       file="x.sql.gz"), capsys)
        assert "--file is relative ('x.sql.gz')" in err
        assert "admin@prod.example.com" in err
        assert "'~'" in err

    def test_export_without_file_passes_nothing(self, registry):
        ev = _extra_vars("export", _args(id="there", wiki="main"))
        assert "file" not in ev

    @pytest.mark.parametrize("command", [
        "gitops_init", "gitops_join", "gitops_push", "gitops_pull"])
    def test_gitops_tilde_ssh_key_unexpanded(self, registry, command):
        ev = _extra_vars(command, _args(id="there", ssh_key="~/.ssh/k"))
        assert ev["ssh_key"] == "~/.ssh/k"

    def test_gitops_relative_ssh_key_refused(self, registry, capsys):
        err = _refused("gitops_pull", _args(id="there", ssh_key=".ssh/k"),
                       capsys)
        assert "--ssh-key is relative ('.ssh/k')" in err

    def test_create_with_host_leaves_build_from_unexpanded(self, registry):
        ev = _extra_vars("create", _args(id="new", wiki="main", path=".",
                                         host="cp", build_from="~/src"))
        assert ev["build_from"] == "~/src"

    def test_create_with_host_refuses_relative_build_from(
            self, registry, capsys):
        err = _refused("create", _args(id="new", wiki="main", path=".",
                                       host="cp", build_from="src"),
                       capsys)
        assert "--build-from is relative ('src')" in err


class TestDirectCommand:
    """gitops status runs without Ansible; main() resolves its --ssh-key
    the same way before dispatch."""

    def _cmd_def(self):
        return next(c for c in _definitions()["commands"]
                    if c["name"] == "gitops_status")

    def test_remote_tilde_unexpanded(self, registry):
        args = _args(id="there", ssh_key="~/.ssh/k")
        canasta.apply_instance_host_paths(self._cmd_def(), args)
        assert args.ssh_key == "~/.ssh/k"

    def test_remote_relative_refused(self, registry, capsys):
        with pytest.raises(SystemExit):
            canasta.apply_instance_host_paths(
                self._cmd_def(), _args(id="there", ssh_key="k"))
        assert "--ssh-key is relative ('k')" in capsys.readouterr().err

    def test_local_relative_resolves_against_cwd(self, registry):
        args = _args(id="here", ssh_key="k")
        canasta.apply_instance_host_paths(self._cmd_def(), args)
        assert args.ssh_key == str(registry / "k")


class TestSidecarAddFile:
    def test_relative_file_resolves_against_cwd(self, registry):
        ev = _extra_vars("sidecar_add", _args(id="there", file="sc.yml"))
        assert ev["file"] == str(registry / "sc.yml")

    def test_dot_relative_file_resolves_against_cwd(self, registry):
        ev = _extra_vars("sidecar_add", _args(id="here", file="./sc.yml"))
        assert ev["file"] == str(registry / "sc.yml")
