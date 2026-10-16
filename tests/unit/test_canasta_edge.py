"""The canasta_edge module: membership, discovery, certificates and staging.

A fake container runner stands in for docker/podman, except in the last
test, which runs the pinned images to create real certificates and check the
rendered configuration.
"""

import os
import shutil
import subprocess

import pytest
import yaml

import canasta_edge as mod

# Pytest imports every test module before running any test, so this is the
# environment the suite started with, before any test exported DOCKER_HOST.
_DOCKER_ENV = dict(os.environ)


class FakeRunner:
    """Records argv; answers ps/inspect from `containers`, creates the files
    an openssl run would, and fails haproxy -c when told to."""

    def __init__(self, containers=None, running=False, invalid=False):
        self.containers = containers or {}
        self.running = running
        self.invalid = invalid
        self.calls = []

    def __call__(self, argv):
        self.calls.append(argv)
        if argv[1] == "ps" and "name=^canasta-edge$" in argv:
            return 0, "edgeid\n" if self.running else "", ""
        if argv[1] == "ps":
            return 0, "".join("%s\n" % c for c in self.containers), ""
        if argv[1] == "inspect":
            return 0, "".join("%s|%s\n" % self.containers[c] for c in argv[4:]), ""
        if argv[1] == "run" and "openssl" in argv[-1]:
            mounts = dict(a.split(":", 1)[::-1] for a in argv if ":/" in a)
            for line in argv[-1].splitlines():
                if line.startswith("cat cert.pem key.pem > /certs/"):
                    name = line.rsplit("/", 1)[1]
                    with open(os.path.join(mounts["/certs"], name), "w") as f:
                        f.write("PEM %s\n" % name)
            return 0, "", ""
        if argv[1] == "run" and "haproxy" in argv:
            return (1, "", "[ALERT] bad config") if self.invalid else (0, "", "")
        return 0, "", ""

    def runs(self, word):
        return [c for c in self.calls if c[1] == "run" and any(word in a for a in c)]


def _instance(root, name, hosts=("example.com",), env=None, media=(), certs=None):
    path = root / name
    (path / "config" / "certs").mkdir(parents=True)
    lines = {"CANASTA_ENABLE_EDGE": "true"}
    lines.update(env or {})
    (path / ".env").write_text("".join("%s=%s\n" % kv for kv in lines.items()))
    (path / "config" / "wikis.yaml").write_text(yaml.safe_dump(
        {"wikis": [{"id": "w%d" % n, "url": h} for n, h in enumerate(hosts)]}))
    (path / "config" / "media-hosts.yaml").write_text(yaml.safe_dump(
        {"media_hosts": [{"hostname": h, "bucket": "b", "endpoint": "e"}
                         for h in media]}))
    for name, content in (certs or {}).items():
        (path / "config" / "certs" / name).write_text(content)
    return str(path)


def _sync(run, edge, inst_id, path, member=None):
    return mod.sync(run, "docker", str(edge), inst_id, path, member)


# --- the instance's layer --------------------------------------------------- #

def test_layer_joins_the_edge_network_under_an_alias(tmp_path):
    path = _instance(tmp_path, "one")
    assert mod.write_layer(path, "one", True)
    layer = yaml.safe_load(open(os.path.join(path, mod.LAYER_FILE)))
    caddy = layer["services"]["caddy"]
    assert caddy["networks"]["canasta-edge"]["aliases"] == ["caddy-one"]
    assert caddy["labels"] == {mod.LABEL_ID: "one", mod.LABEL_PATH: path}
    assert layer["networks"]["canasta-edge"] == {"external": True}
    assert not mod.write_layer(path, "one", True)
    assert mod.write_layer(path, "one", False)
    assert not os.path.exists(os.path.join(path, mod.LAYER_FILE))


# --- membership --------------------------------------------------------------- #

def test_an_instance_not_behind_the_edge_creates_nothing(tmp_path):
    path = _instance(tmp_path, "one", env={"CANASTA_ENABLE_EDGE": "false"})
    run = FakeRunner()
    result = _sync(run, tmp_path / "edge", "one", path)
    assert result["members"] == []
    assert not (tmp_path / "edge").exists()
    assert run.calls == []


def test_joining_renders_the_edge(tmp_path):
    path = _instance(tmp_path, "one")
    run = FakeRunner()
    edge = tmp_path / "edge"
    result = _sync(run, edge, "one", path)
    assert result["members"] == ["one"] and result["reload"]
    assert not result["running"]
    assert yaml.safe_load((edge / "members.yaml").read_text()) == {
        "members": [{"id": "one", "path": path}]}
    assert "caddy-one:80" in (edge / "conf" / "haproxy.cfg").read_text()
    assert "container_name: canasta-edge" in (edge / "docker-compose.yml").read_text()
    assert os.path.exists(os.path.join(path, mod.LAYER_FILE))
    assert sorted(os.listdir(edge / "conf" / "certs")) == ["default.pem", "example.com.pem"]


def test_a_second_sync_with_nothing_changed_needs_no_reload(tmp_path):
    path = _instance(tmp_path, "one")
    edge = tmp_path / "edge"
    _sync(FakeRunner(), edge, "one", path)
    run = FakeRunner(running=True)
    result = _sync(run, edge, "one", path)
    assert not result["reload"] and not result["changed"]
    assert run.runs("haproxy") == [] and run.runs("openssl") == []


def test_members_found_running_are_kept(tmp_path):
    """A member another controller manages is found by its labels."""
    other = _instance(tmp_path, "other", hosts=("other.example.org",))
    path = _instance(tmp_path, "one")
    run = FakeRunner(containers={"c1": ("other", other), "edge": ("", "")})
    result = _sync(run, tmp_path / "edge", "one", path)
    assert result["members"] == ["one", "other"]
    hosts = (tmp_path / "edge" / "conf" / "hosts.map").read_text()
    assert "other.example.org other/wiki" in hosts


def test_stale_members_are_dropped(tmp_path):
    gone = str(tmp_path / "gone")
    off = _instance(tmp_path, "off", hosts=("off.example.org",))
    path = _instance(tmp_path, "one")
    edge = tmp_path / "edge"
    edge.mkdir()
    mod.write_members(str(edge), {"gone": gone, "off": off})
    with open(os.path.join(off, ".env"), "w") as f:
        f.write("CANASTA_ENABLE_EDGE=false\n")
    assert _sync(FakeRunner(), edge, "one", path)["members"] == ["one"]


def test_leaving_removes_the_route_and_the_certificate(tmp_path):
    one = _instance(tmp_path, "one")
    two = _instance(tmp_path, "two", hosts=("two.example.org",))
    edge = tmp_path / "edge"
    _sync(FakeRunner(), edge, "one", one)
    _sync(FakeRunner(), edge, "two", two)
    result = _sync(FakeRunner(running=True), edge, "two", two, member="absent")
    assert result["members"] == ["one"] and result["reload"]
    assert "two.example.org" not in (edge / "conf" / "hosts.map").read_text()
    assert "two.example.org.pem" not in os.listdir(edge / "conf" / "certs")
    assert not os.path.exists(os.path.join(two, mod.LAYER_FILE))


def test_the_last_member_leaving_empties_the_edge(tmp_path):
    one = _instance(tmp_path, "one")
    edge = tmp_path / "edge"
    _sync(FakeRunner(), edge, "one", one)
    result = _sync(FakeRunner(running=True), edge, "one", one, member="absent")
    assert result["members"] == [] and result["running"]


# --- certificates ------------------------------------------------------------- #

def test_custom_certificate_is_assembled_from_the_instance(tmp_path):
    path = _instance(tmp_path, "one", env={"CADDY_TLS_MODE": "custom"},
                     certs={"origin.crt": "CERT", "origin.key": "KEY\n"})
    edge = tmp_path / "edge"
    _sync(FakeRunner(), edge, "one", path)
    pem = edge / "conf" / "certs" / "custom-one.pem"
    assert pem.read_text() == "CERT\nKEY\n"
    assert oct(pem.stat().st_mode & 0o777) == "0o600"


def test_missing_custom_certificate_is_refused(tmp_path):
    path = _instance(tmp_path, "one", env={"CADDY_TLS_MODE": "custom"})
    with pytest.raises(mod.EdgeError, match="origin.crt or origin.key is missing"):
        _sync(FakeRunner(), tmp_path / "edge", "one", path)


def test_media_entries_without_a_hostname_are_skipped(tmp_path):
    path = _instance(tmp_path, "one", media=("images.example.com",))
    media = os.path.join(path, "config", "media-hosts.yaml")
    data = yaml.safe_load(open(media))
    data["media_hosts"].append({"hostname": "", "bucket": "b", "endpoint": "e"})
    with open(media, "w") as f:
        yaml.safe_dump(data, f)
    _sync(FakeRunner(), tmp_path / "edge", "one", path)
    assert "images.example.com one/media" in (
        tmp_path / "edge" / "conf" / "hosts.map").read_text()


# --- validation --------------------------------------------------------------- #

def test_an_invalid_render_is_not_installed(tmp_path):
    path = _instance(tmp_path, "one")
    edge = tmp_path / "edge"
    with pytest.raises(mod.EdgeError, match="bad config"):
        _sync(FakeRunner(invalid=True), edge, "one", path)
    assert not (edge / "conf" / "haproxy.cfg").exists()
    assert not [n for n in os.listdir(edge) if n.startswith(".stage-")]


def test_a_render_error_names_the_instance(tmp_path):
    path = _instance(tmp_path, "one", hosts=("example.com:8443",))
    with pytest.raises(mod.EdgeError, match="Instance 'one'.*has a port"):
        _sync(FakeRunner(), tmp_path / "edge", "one", path)


# --- the real images ---------------------------------------------------------- #

def _docker_ok():
    if not shutil.which("docker"):
        return False
    return subprocess.run(["docker", "info"], capture_output=True,
                          env=_DOCKER_ENV).returncode == 0


@pytest.mark.skipif(not _docker_ok(), reason="Docker is not available")
def test_certificates_and_validation_with_the_pinned_images(tmp_path):
    def run(argv):
        proc = subprocess.run(argv, capture_output=True, text=True, env=_DOCKER_ENV)
        return proc.returncode, proc.stdout, proc.stderr

    path = _instance(tmp_path, "one", hosts=("example.com", "www.example.com"),
                     media=("images.example.com",))
    edge = tmp_path / "edge"
    result = _sync(run, edge, "one", path)
    assert result["members"] == ["one"]
    certs = edge / "conf" / "certs"
    out = subprocess.run(
        ["docker", "run", "--rm", "-v", "%s:/certs:ro" % certs,
         "-v", "%s:/ca:ro" % (edge / "ca"), "--entrypoint", "sh", mod.ACME_IMAGE,
         "-c", "openssl verify -CAfile /ca/ca.crt /certs/www.example.com.pem && "
               "openssl x509 -in /certs/www.example.com.pem -noout -ext subjectAltName"],
        capture_output=True, text=True, env=_DOCKER_ENV)
    assert out.returncode == 0, out.stderr
    assert "DNS:www.example.com" in out.stdout
