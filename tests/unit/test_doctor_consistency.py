"""Tests for `canasta doctor`'s per-instance config<->runtime consistency
checks — the detection half of the drift class from the upgrade incident
(profiles out of sync with flags, containers running unmanaged, CirrusSearch
configured while Elasticsearch is disabled)."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from direct_commands import doctor  # noqa: E402


def _w(env, profiles, running, cirrus):
    return doctor._consistency_warnings(env, profiles, running, cirrus)


class TestConsistencyWarnings:
    def test_clean_instance_has_no_warnings(self):
        env = {
            "CANASTA_ENABLE_ELASTICSEARCH": "true",
            "CANASTA_ENABLE_CROWDSEC": "true",
            "COMPOSE_PROFILES": "internal-db,varnish,crowdsec,elasticsearch",
        }
        profiles = ["internal-db", "varnish", "crowdsec", "elasticsearch"]
        running = ["web", "caddy", "varnish", "crowdsec", "db", "elasticsearch"]
        assert _w(env, profiles, running, True) == []

    def test_todays_incident_flags_every_drift(self):
        # ES flag false + COMPOSE_PROFILES missing internal-db/elasticsearch,
        # but db + elasticsearch are running and CirrusSearch is configured.
        env = {
            "CANASTA_ENABLE_ELASTICSEARCH": "false",
            "CANASTA_ENABLE_CROWDSEC": "true",
            "COMPOSE_PROFILES": "varnish,crowdsec",
        }
        profiles = ["varnish", "crowdsec"]
        running = ["web", "caddy", "varnish", "crowdsec", "db", "elasticsearch"]
        out = _w(env, profiles, running, True)
        joined = " ".join(out)
        assert "should add internal-db" in joined         # profile drift
        assert any("'db'" in o for o in out)              # db unmanaged
        assert any("'elasticsearch'" in o for o in out)   # ES unmanaged
        assert any("CirrusSearch" in o for o in out)      # search backend
        assert len(out) == 4

    def test_self_heal_internal_db_drift_only(self):
        env = {"COMPOSE_PROFILES": "varnish"}
        out = _w(env, ["varnish"], ["web", "varnish"], False)
        assert len(out) == 1
        assert "should add internal-db" in out[0]

    def test_external_db_does_not_warn_about_internal_db(self):
        env = {
            "USE_EXTERNAL_DB": "true",
            "CANASTA_ENABLE_CROWDSEC": "true",
            "COMPOSE_PROFILES": "varnish,crowdsec",
        }
        out = _w(env, ["varnish", "crowdsec"], ["web", "varnish", "crowdsec"], False)
        assert out == []

    def test_cirrus_without_elasticsearch_warns(self):
        env = {"COMPOSE_PROFILES": "internal-db,varnish"}
        # internal-db + varnish are the full derived set here, so no drift;
        # only the CirrusSearch<->ES mismatch should fire.
        out = _w(env, ["internal-db", "varnish"], ["web", "varnish", "db"], True)
        assert len(out) == 1
        assert "CirrusSearch" in out[0]


class TestServiceProfileMap:
    def test_managed_services_map_to_profiles(self):
        assert doctor._SERVICE_PROFILE["db"] == "internal-db"
        assert doctor._SERVICE_PROFILE["elasticsearch"] == "elasticsearch"
        assert doctor._SERVICE_PROFILE["varnish"] == "varnish"
        assert doctor._SERVICE_PROFILE["crowdsec"] == "crowdsec"

    def test_always_on_services_are_not_profile_gated(self):
        assert "web" not in doctor._SERVICE_PROFILE
        assert "caddy" not in doctor._SERVICE_PROFILE


class TestGatherRuntime:
    def test_parses_services_and_cirrus_flag(self, monkeypatch):
        d = doctor._helpers._SENTINEL
        out = "web\nvarnish\ndb\n%s\nUSES_CIRRUS\n" % d

        class _R:
            stdout = out

        monkeypatch.setattr(doctor._helpers, "_is_localhost", lambda h: True)
        monkeypatch.setattr(doctor.subprocess, "run", lambda *a, **k: _R())
        running, cirrus = doctor._gather_runtime("/srv/x", "localhost")
        assert running == ["web", "varnish", "db"]
        assert cirrus is True

    def test_no_cirrus_flag(self, monkeypatch):
        d = doctor._helpers._SENTINEL

        class _R:
            stdout = "web\n%s\nNO_CIRRUS\n" % d

        monkeypatch.setattr(doctor._helpers, "_is_localhost", lambda h: True)
        monkeypatch.setattr(doctor.subprocess, "run", lambda *a, **k: _R())
        running, cirrus = doctor._gather_runtime("/srv/x", "localhost")
        assert running == ["web"]
        assert cirrus is False


class TestInstanceConsistencyLines:
    def test_skips_when_no_instance_or_k8s(self):
        assert doctor._instance_consistency_lines(None) == []
        assert doctor._instance_consistency_lines(
            {"orchestrator": "kubernetes", "path": "/x"}) == []

    def test_compose_instance_reports_warnings(self, monkeypatch):
        inst = {"id": "site", "orchestrator": "compose", "path": "/srv/site",
                "host": "localhost"}
        monkeypatch.setattr(
            doctor._helpers, "_read_env_content",
            lambda path, host: "CANASTA_ENABLE_ELASTICSEARCH=false\n"
                               "COMPOSE_PROFILES=varnish\n")
        monkeypatch.setattr(
            doctor, "_gather_runtime",
            lambda path, host: (["web", "varnish", "db"], True))
        lines = doctor._instance_consistency_lines(inst)
        assert lines[1] == "Instance consistency (site):"
        body = " ".join(lines)
        assert "WARN" in body
        assert "internal-db" in body      # drift
        assert "CirrusSearch" in body     # search backend
        assert "canasta reconcile" in body  # points at the fix command

    def test_compose_instance_clean_reports_ok(self, monkeypatch):
        inst = {"id": "site", "orchestrator": "compose", "path": "/srv/site",
                "host": "localhost"}
        monkeypatch.setattr(
            doctor._helpers, "_read_env_content",
            lambda path, host: "CANASTA_ENABLE_CROWDSEC=true\n"
                               "COMPOSE_PROFILES=internal-db,varnish,crowdsec\n")
        monkeypatch.setattr(
            doctor, "_gather_runtime",
            lambda path, host: (["web", "varnish", "crowdsec", "db"], False))
        lines = doctor._instance_consistency_lines(inst)
        assert any("OK (" in line for line in lines)
