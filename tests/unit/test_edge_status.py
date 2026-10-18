"""What `canasta doctor` says about the HAProxy edge."""

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)

from direct_commands import _helpers, doctor  # noqa: E402

S = _helpers._SENTINEL


def _probe(edge="Up 2 hours", acme="Up 2 hours", members=("one",), certs=(), spoa=""):
    rows = ["Main_Domain|KeyLength|SAN_Domains|Profile|CA|Created|Renew"]
    rows += ["%s|ec-256|no||%s|2026-10-17T03:27:34Z|%s" % c for c in certs]
    return ("%sEDGE:%s\n%sACME:%s\n%sSPOA:%s\n%sMEMBERS:\nmembers:\n%s%sCERTS:\n%s\n"
            % (S, edge, S, acme, S, spoa, S,
               "".join("- id: %s\n  path: /srv/%s\n" % (m, m) for m in members),
               S, "\n".join(rows)))


def _lines(stdout, tls_mode="acme", names=("example.com",), inst="one", crowdsec=None):
    return "\n".join(doctor._edge_lines_from_probe(
        inst, tls_mode, list(names), doctor._parse_edge_probe(stdout), crowdsec))


def test_a_healthy_edge_instance():
    out = _lines(_probe(certs=[("example.com", "LetsEncrypt.org", "2026-12-16T03:27:33Z")]))
    assert "edge: running (Up 2 hours)" in out and "member: yes" in out
    assert "acme.sh: running" in out
    assert "example.com: Let's Encrypt production, renews 2026-12-16T03:27:33Z" in out
    assert "WARN" not in out


def test_a_stopped_edge_and_acme_are_warnings():
    out = _lines(_probe(edge="", acme=""))
    assert "edge: WARN — not running" in out
    assert "acme.sh: WARN — not running" in out


def test_a_missing_member_and_certificate_are_warnings():
    out = _lines(_probe(members=("other",)), names=("example.com", "www.example.com"))
    assert "member: WARN" in out and "canasta restart -i one" in out
    assert "www.example.com: WARN — no Let's Encrypt certificate yet" in out


def test_staging_is_named():
    out = _lines(_probe(certs=[("example.com", "LetsEncrypt.org_test", "2026-11-20T00:00:00Z")]))
    assert "Let's Encrypt staging" in out


def test_internal_mode_skips_acme():
    out = _lines(_probe(acme=""), tls_mode="internal")
    assert "acme.sh" not in out and "Let's Encrypt" not in out


def test_the_probe_script_runs(tmp_path, monkeypatch):
    """The real script, against a fake container CLI and config directory.
    The delimiter begins with dashes, which printf would take for options
    if it were the format string."""
    import subprocess
    fake = tmp_path / "fake-docker"
    fake.write_text(
        "#!/bin/sh\n"
        'case "$*" in\n'
        '  *"name=^canasta-edge\\$"*) echo "Up 5 minutes";;\n'
        '  *"name=^canasta-edge-acme\\$"*) echo "Up 5 minutes";;\n'
        '  *"service=crowdsec-spoa"*) echo "Up 4 minutes";;\n'
        '  *"--listraw"*) echo "Main_Domain|KeyLength|SAN_Domains|Profile|CA|Created|Renew";'
        ' echo "example.com|ec-256|no||LetsEncrypt.org|2026-10-17T00:00:00Z|2026-12-16T00:00:00Z";;\n'
        "esac\n")
    fake.chmod(0o755)
    edge = tmp_path / "config" / "canasta" / "edge"
    edge.mkdir(parents=True)
    (edge / "members.yaml").write_text("members:\n- id: one\n  path: /srv/one\n")
    script = doctor._EDGE_SCRIPT % {"delim": S, "inspect": str(fake), "project": "one"}
    env = dict(os.environ, XDG_CONFIG_HOME=str(tmp_path / "config"))
    out = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                         env=env).stdout
    lines = _lines(out, crowdsec=True)
    assert "WARN" not in lines, lines
    assert "CrowdSec bouncer: running (Up 4 minutes)" in lines
    assert "example.com: Let's Encrypt production" in lines


def test_the_crowdsec_bouncer_is_reported():
    assert "CrowdSec" not in _lines(_probe())
    assert "CrowdSec bouncer: running (Up 1 hour)" in _lines(_probe(spoa="Up 1 hour"), crowdsec=True)
    down = _lines(_probe(), crowdsec=True)
    assert "CrowdSec bouncer: WARN — not running" in down and "canasta restart -i one" in down
    assert "CrowdSec bouncer: WARN — not enrolled yet" in _lines(_probe(), crowdsec=False)


def test_a_plain_http_instance_without_a_trusted_proxy_is_warned():
    out = "\n".join(doctor._edge_lines_from_probe(
        "one", "acme", ["example.com"], doctor._parse_edge_probe(_probe()), None, True, False))
    assert "client addresses: WARN" in out and "TRUSTED_PROXIES" in out
    assert "Let's Encrypt" not in out and "acme.sh" not in out
    trusted = "\n".join(doctor._edge_lines_from_probe(
        "one", "acme", ["example.com"], doctor._parse_edge_probe(_probe()), None, True, True))
    assert "WARN" not in trusted


def test_rootless_docker_is_warned_and_rootless_podman_is_not():
    probe = doctor._parse_edge_probe(_probe() + "%sDAEMON:[\"name=seccomp\",\"name=rootless\"]\n" % S)
    docker = "\n".join(doctor._edge_lines_from_probe("one", "acme", [], probe, docker=True))
    assert "rootless Docker" in docker and "no per-client rate limits" in docker
    rated = "\n".join(doctor._edge_lines_from_probe("one", "acme", [], probe, docker=True,
                                                    rates_set=True))
    assert "rootless Docker" in rated and "no per-client rate limits" not in rated
    podman = "\n".join(doctor._edge_lines_from_probe("one", "acme", [], probe, docker=False))
    assert "rootless" not in podman
