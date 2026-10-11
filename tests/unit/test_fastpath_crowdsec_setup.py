"""The Compose fast paths leave CrowdSec setup to the Ansible start."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
import direct_commands  # noqa: E402
from direct_commands import _helpers  # noqa: E402

_ON = {"CANASTA_ENABLE_CROWDSEC": "true", "CROWDSEC_BOUNCER_API_KEY": "k"}


def _inst(path, host="localhost"):
    return {"path": str(path), "orchestrator": "compose", "host": host}


def _write_crowdsec_files(path, *names):
    d = path / "config" / "crowdsec"
    d.mkdir(parents=True, exist_ok=True)
    for name in names:
        (d / name).write_text("x\n")


class TestCrowdsecSetupPending:
    def test_disabled_needs_nothing(self, tmp_path):
        env = {"CANASTA_ENABLE_CROWDSEC": "false"}
        assert not _helpers._crowdsec_setup_pending(_inst(tmp_path), env)
        assert not _helpers._crowdsec_setup_pending(_inst(tmp_path), {})

    def test_no_bouncer_key(self, tmp_path):
        _write_crowdsec_files(tmp_path, "acquis.yaml", "whitelists.yaml")
        env = {"CANASTA_ENABLE_CROWDSEC": "true",
               "CROWDSEC_BOUNCER_API_KEY": ""}
        assert _helpers._crowdsec_setup_pending(_inst(tmp_path), env)

    def test_enrolled_with_files_needs_nothing(self, tmp_path):
        _write_crowdsec_files(tmp_path, "acquis.yaml", "whitelists.yaml")
        assert not _helpers._crowdsec_setup_pending(_inst(tmp_path), _ON)

    def test_missing_file(self, tmp_path):
        _write_crowdsec_files(tmp_path, "acquis.yaml")
        assert _helpers._crowdsec_setup_pending(_inst(tmp_path), _ON)

    def test_directory_in_place_of_file(self, tmp_path):
        _write_crowdsec_files(tmp_path, "acquis.yaml")
        (tmp_path / "config" / "crowdsec" / "whitelists.yaml").mkdir()
        assert _helpers._crowdsec_setup_pending(_inst(tmp_path), _ON)

    @pytest.mark.parametrize("rc,pending", [(0, False), (1, True)])
    def test_remote_tests_both_files(self, monkeypatch, rc, pending):
        cmds = []
        monkeypatch.setattr(_helpers, "_ssh_run",
                            lambda host, cmd: cmds.append((host, cmd))
                            or (rc, ""))
        inst = _inst("/srv/site", host="prod1")
        assert _helpers._crowdsec_setup_pending(inst, _ON) is pending
        assert cmds == [(
            "prod1",
            "test -f '/srv/site/config/crowdsec/acquis.yaml' && "
            "test -f '/srv/site/config/crowdsec/whitelists.yaml'",
        )]


@pytest.fixture
def fast_path(monkeypatch):
    """Stub everything around the decision; record compose actions."""
    state = {"env": {}, "pending": False, "compose": []}
    monkeypatch.setattr(_helpers, "_resolve_instance",
                        lambda args: ("test", _inst("/srv/test")))
    monkeypatch.setattr(_helpers, "_instance_has_sidecars", lambda inst: False)
    monkeypatch.setattr(_helpers, "_sync_compose_profiles",
                        lambda inst: state["env"])
    monkeypatch.setattr(_helpers, "_backfill_db_defaults", lambda inst: None)
    monkeypatch.setattr(_helpers, "_check_running_compose", lambda *a: False)
    monkeypatch.setattr(_helpers, "_missing_db_password",
                        lambda inst, env: False)
    monkeypatch.setattr(_helpers, "_crowdsec_setup_pending",
                        lambda inst, env: state["pending"])
    monkeypatch.setattr(_helpers, "_wait_web_ready", lambda i, inst: 0)
    monkeypatch.setattr(_helpers, "_rootless_operator_chown",
                        lambda inst: None)
    monkeypatch.setattr(_helpers, "_run_compose",
                        lambda i, inst, argv, **kw:
                        state["compose"].append(argv[0]) or 0)
    monkeypatch.setattr(direct_commands.rebuild, "_list_buildable_services",
                        lambda inst, include_sidecars=False: ["web"])
    return state


def _args(inst_id=None):
    return type("Args", (), {"id": inst_id, "no_cache": False,
                             "no_restart": False})()


class TestStart:
    def test_falls_back_when_crowdsec_setup_pending(self, fast_path):
        fast_path["pending"] = True
        assert direct_commands.cmd_start(_args("test")) is (
            direct_commands.FALLBACK)
        assert fast_path["compose"] == []

    def test_stays_fast_when_nothing_pending(self, fast_path):
        assert direct_commands.cmd_start(_args("test")) == 0
        assert fast_path["compose"] == ["up"]


class TestRebuild:
    def test_hands_restart_to_ansible_when_crowdsec_setup_pending(
            self, fast_path):
        fast_path["pending"] = True
        args = _args()
        result = direct_commands.cmd_rebuild(args)
        assert isinstance(result, direct_commands.AnsibleHandoff)
        assert result.command == "restart"
        assert args.id == "test"
        assert fast_path["compose"] == ["build"]

    def test_hands_restart_to_ansible_for_wiki_db_account(self, fast_path):
        fast_path["env"] = {"WIKI_DB_USER": "mediawiki"}
        result = direct_commands.cmd_rebuild(_args("test"))
        assert isinstance(result, direct_commands.AnsibleHandoff)
        assert fast_path["compose"] == ["build"]

    def test_hands_restart_to_ansible_for_missing_db_password(
            self, fast_path, monkeypatch):
        monkeypatch.setattr(_helpers, "_missing_db_password",
                            lambda inst, env: True)
        result = direct_commands.cmd_rebuild(_args("test"))
        assert isinstance(result, direct_commands.AnsibleHandoff)
        assert fast_path["compose"] == ["build"]

    def test_restarts_itself_when_nothing_pending(self, fast_path):
        fast_path["env"] = {"WIKI_DB_USER": "root"}
        assert direct_commands.cmd_rebuild(_args("test")) == 0
        assert fast_path["compose"] == ["build", "down", "up"]
