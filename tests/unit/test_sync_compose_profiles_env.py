"""_sync_compose_profiles hands back the .env it parsed, so its caller need
not read the file again."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

import direct_commands


class TestSyncComposeProfilesHandsBackWhatItParsed:
    def _inst(self, tmp_path):
        return {"path": str(tmp_path), "host": "localhost"}

    def test_it_returns_the_parsed_env_when_nothing_changed(self, tmp_path):
        (tmp_path / ".env").write_text(
            "COMPOSE_PROFILES=varnish\nMYSQL_PASSWORD=s3cret\n")
        env = direct_commands._helpers._sync_compose_profiles(self._inst(tmp_path))
        assert env["MYSQL_PASSWORD"] == "s3cret"

    def test_it_returns_the_parsed_env_when_it_rewrote_profiles(self, tmp_path):
        (tmp_path / ".env").write_text(
            "CANASTA_ENABLE_REDIS=true\nCOMPOSE_PROFILES=\n"
            "MYSQL_PASSWORD=s3cret\n")
        env = direct_commands._helpers._sync_compose_profiles(self._inst(tmp_path))
        assert "redis" in (tmp_path / ".env").read_text()
        assert env["MYSQL_PASSWORD"] == "s3cret"

    def test_no_env_file_yields_an_empty_mapping(self, tmp_path):
        # Falsy, so the caller treats it as "nothing to judge on" rather
        # than as a missing password.
        assert direct_commands._helpers._sync_compose_profiles(
            self._inst(tmp_path)) == {}
