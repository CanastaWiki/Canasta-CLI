"""port_listeners.sh: the host's listeners on the given ports, from ss or
lsof on the host, or from netstat in a container on the host's network
when the CLI runs in a container or the host has neither tool."""

import os
import re
import stat
import subprocess

import pytest
import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
SCRIPT = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "files", "scripts",
    "port_listeners.sh")
EDGE_SYNC = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "edge_sync.yml")
RENDER = os.path.join(
    REPO_ROOT, "roles", "common", "module_utils", "canasta_edge_render.py")
IMAGES = os.path.join(REPO_ROOT, "vars", "images.yml")

NETSTAT = """Active Internet connections (only servers)
Proto Recv-Q Send-Q Local Address           Foreign Address         State
tcp        0      0 0.0.0.0:443             0.0.0.0:*               LISTEN
tcp        0      0 0.0.0.0:8443            0.0.0.0:*               LISTEN
tcp        0      0 127.0.0.1:3306          0.0.0.0:*               LISTEN
tcp        0      0 :::80                   :::*                    LISTEN
"""

pytestmark = pytest.mark.skipif(
    os.path.exists("/.dockerenv") or os.path.exists("/run/.containerenv"),
    reason="the script treats a container as having no usable ss or lsof")


def _tool(bindir, name, body):
    path = bindir / name
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _run(tmp_path, tools, *ports):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name in ("awk", "tail", "cat"):
        for d in ("/usr/bin", "/bin"):
            if os.path.exists(os.path.join(d, name)):
                os.symlink(os.path.join(d, name), bindir / name)
                break
    for name, body in tools.items():
        _tool(bindir, name, body)
    netstat = tmp_path / "netstat.txt"
    netstat.write_text(NETSTAT)
    proc = subprocess.run(
        ["/bin/bash", SCRIPT, "docker", "probe:1", *ports],
        capture_output=True, text=True,
        env={"PATH": str(bindir), "NETSTAT_OUT": str(netstat),
             "ARGS_OUT": str(tmp_path / "args.txt")})
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def test_ss_is_asked_about_every_port(tmp_path):
    out = _run(tmp_path, {"ss": 'echo "$@" > "$ARGS_OUT"; echo held\n'},
               "80", "443")
    assert out == "held\n"
    assert (tmp_path / "args.txt").read_text().strip() == (
        "-tlnpH sport = :80 or sport = :443")


def test_lsof_when_ss_is_missing(tmp_path):
    out = _run(tmp_path, {"lsof": 'echo "$@" > "$ARGS_OUT"; '
                                  'printf "COMMAND\\\\nnginx\\\\n"\n'},
               "80", "443")
    assert out == "nginx\n"
    assert (tmp_path / "args.txt").read_text().strip() == (
        "-nP -iTCP:80 -iTCP:443 -sTCP:LISTEN")


def test_container_on_the_host_network_when_neither_tool_exists(tmp_path):
    out = _run(tmp_path, {"docker": 'echo "$@" > "$ARGS_OUT"; '
                                    'cat "$NETSTAT_OUT"\n'},
               "80", "443")
    lines = out.splitlines()
    assert len(lines) == 2
    assert lines[0].split()[3] == ":::80"
    assert lines[1].split()[3] == "0.0.0.0:443"
    assert (tmp_path / "args.txt").read_text().strip() == (
        "run --rm --network host --entrypoint netstat probe:1 -tln")


def test_a_free_port_prints_nothing(tmp_path):
    # 8443 must not count as 443, nor 3306 as 306.
    assert _run(tmp_path, {"docker": 'cat "$NETSTAT_OUT"\n'},
                "306", "5000") == ""


def test_unavailable_when_nothing_can_look(tmp_path):
    out = _run(tmp_path, {"docker": "exit 1\n"}, "80")
    assert out.strip() == "CANASTA_PORT_PROBE_UNAVAILABLE"


def test_probe_image_is_the_edge_acme_image():
    # The edge host has already pulled it.
    with open(IMAGES) as f:
        image = yaml.safe_load(f)["canasta_port_probe_image"]
    with open(RENDER) as f:
        acme = re.search(r'^ACME_IMAGE = "([^"]+)"', f.read(), re.M).group(1)
    assert image == acme


def test_edge_sync_uses_the_script_and_ignores_the_sentinel():
    with open(EDGE_SYNC) as f:
        tasks = {t.get("name"): t for t in yaml.safe_load(f)}
    probe = tasks["Find what else listens on the edge's ports"]
    assert "port_listeners.sh" in probe["ansible.builtin.script"]["cmd"]
    gate = tasks["Refuse to start the edge while its ports are taken"]
    assert any("CANASTA_PORT_PROBE_UNAVAILABLE" in w for w in gate["when"])
