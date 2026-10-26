"""The canasta_edge module: membership, discovery, certificates and staging.

A fake container runner stands in for docker/podman, except in the last
test, which runs the pinned images to create real certificates and check the
rendered configuration.
"""

import json
import os
import re
import shutil
import subprocess

import pytest
import yaml

import canasta_edge as mod

# Pytest imports every test module before running any test, so this is the
# environment the suite started with, before any test exported DOCKER_HOST.
_DOCKER_ENV = dict(os.environ)
# Before the fixture below stands in for it.
_CHALLENGE_ANSWERED = mod.challenge_answered


class FakeRunner:
    """Records argv; answers ps/inspect from `containers`, creates the files
    an openssl run would, and fails haproxy -c when told to."""

    def __init__(self, containers=None, running=False, invalid=False,
                 listed=None, issue_fails=(), network_ipv6=None, stats_down=0,
                 daemon_id="", unpulled=()):
        self.containers = containers or {}
        # Images that are absent and fail to pull.
        self.unpulled = set(unpulled)
        self.daemon_id = daemon_id
        # How many stats socket checks fail before HAProxy answers.
        self.stats_down = stats_down
        # The existing edge network's IPv6 setting; None when there is none.
        self.network_ipv6 = network_ipv6
        self.running = running
        self.invalid = invalid
        # acme.sh's certificates: {hostname: (CA, issued)}
        self.listed = dict(listed or {})
        self.issue_fails = set(issue_fails)
        self.calls = []
        self.envs = []

    def acme_calls(self, word):
        return [c for c in self.calls if c[1] == "exec" and word in c]

    def _acme(self, args):
        if args[0] == "--list":
            rows = ["Main_Domain|KeyLength|SAN_Domains|Profile|CA|Created|Renew"]
            rows += ["%s|ec-256|no||%s|%s|" % (h, ca, "2026-10-17" if done else "")
                     for h, (ca, done) in self.listed.items()]
            return 0, "\n".join(rows) + "\n", ""
        host = args[args.index("-d") + 1]
        if args[0] == "--issue":
            if host in self.issue_fails:
                return 1, "", "Verify error: Invalid response from http://%s" % host
            staging = args[args.index("--server") + 1] == "letsencrypt_test"
            self.listed[host] = (mod.ACME_CA_NAMES[staging], True)
        if args[0] == "--remove":
            self.listed.pop(host, None)
        return 0, "", ""

    def __call__(self, argv, env=None, cwd=None):
        self.calls.append(argv)
        self.envs.append(env or {})
        if argv[1:3] == ["image", "inspect"]:
            return (1, "", "No such image") if argv[3] in self.unpulled else (0, "", "")
        if argv[1] == "pull" and argv[-1] in self.unpulled:
            return 1, "", "reading manifest: received unexpected HTTP status: 502 Bad Gateway"
        if argv[1] == "exec" and "acme.sh" in argv:
            return self._acme(argv[argv.index("acme.sh") + 1:])
        if argv[1] == "exec" and "socat" in argv[-1]:
            if self.stats_down:
                self.stats_down -= 1
                return 1, "", "connect: No such file or directory"
            return 0, "", ""
        if argv[1] == "exec":
            return 0, "", ""
        if argv[1] == "info" and "{{.ID}}" in argv:
            return 0, "%s\n" % self.daemon_id, ""
        if argv[1:3] == ["network", "inspect"]:
            if self.network_ipv6 is None:
                return 1, "", "network canasta-edge not found"
            return 0, "%s\n" % str(self.network_ipv6).lower(), ""
        if argv[1] == "ps" and "name=^canasta-edge$" in argv:
            return 0, "edgeid\n" if self.running else "", ""
        if argv[1] == "ps" and "name=^canasta-edge-acme$" in argv:
            return 0, "acmeid\n" if self.running else "", ""
        if argv[1] == "run" and "--register-account" in argv[-1]:
            return 0, "", "[x] ACCOUNT_THUMBPRINT='THUMB123'\n" * 2
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
    lines = {}
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


@pytest.fixture(autouse=True)
def _no_host_ipv6(monkeypatch):
    # The test host's own IPv6 must not change what is rendered.
    monkeypatch.setattr(mod, "host_has_ipv6", lambda: False)


@pytest.fixture(autouse=True)
def _challenges_answered(monkeypatch):
    # No HAProxy listens on the test host's ports.
    monkeypatch.setattr(mod, "challenge_answered", lambda port, thumbprint: True)


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
    path = _instance(tmp_path, "one")
    run = FakeRunner()
    result = _sync(run, tmp_path / "edge", "one", path, member="absent")
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
    assert "other.example.org@443 other/wiki" in hosts


def test_stale_members_are_dropped(tmp_path):
    gone = str(tmp_path / "gone")
    other = _instance(tmp_path, "other", hosts=("other.example.org",))
    path = _instance(tmp_path, "one")
    edge = tmp_path / "edge"
    edge.mkdir()
    mod.write_members(str(edge), {"gone": gone, "other": other})
    assert _sync(FakeRunner(), edge, "one", path)["members"] == ["one", "other"]


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
    path = _instance(tmp_path, "one", env={"TLS_MODE": "custom"},
                     certs={"origin.crt": "CERT", "origin.key": "KEY\n"})
    edge = tmp_path / "edge"
    _sync(FakeRunner(), edge, "one", path)
    pem = edge / "conf" / "certs" / "custom-one.pem"
    assert pem.read_text() == "CERT\nKEY\n"
    assert oct(pem.stat().st_mode & 0o777) == "0o600"


def test_missing_custom_certificate_is_refused(tmp_path):
    path = _instance(tmp_path, "one", env={"TLS_MODE": "custom"})
    result = _sync(FakeRunner(), tmp_path / "edge", "one", path)
    assert re.search("origin.crt or origin.key is missing", result["error"])


def test_media_entries_without_a_hostname_are_skipped(tmp_path):
    path = _instance(tmp_path, "one", media=("images.example.com",))
    media = os.path.join(path, "config", "media-hosts.yaml")
    data = yaml.safe_load(open(media))
    data["media_hosts"].append({"hostname": "", "bucket": "b", "endpoint": "e"})
    with open(media, "w") as f:
        yaml.safe_dump(data, f)
    _sync(FakeRunner(), tmp_path / "edge", "one", path)
    assert "images.example.com@443 one/media" in (
        tmp_path / "edge" / "conf" / "hosts.map").read_text()


# --- validation --------------------------------------------------------------- #

def test_an_invalid_render_is_not_installed(tmp_path):
    path = _instance(tmp_path, "one")
    edge = tmp_path / "edge"
    assert re.search("bad config", _sync(FakeRunner(invalid=True), edge, "one", path)["error"])
    assert not (edge / "conf" / "haproxy.cfg").exists()
    assert not [n for n in os.listdir(edge) if n.startswith(".stage-")]


def test_a_failed_haproxy_pull_is_reported_as_one(tmp_path):
    path = _instance(tmp_path, "one")
    edge = tmp_path / "edge"
    run = FakeRunner(unpulled={mod.HAPROXY_IMAGE})
    error = _sync(run, edge, "one", path)["error"]
    assert error == "Could not pull %s: reading manifest: received unexpected HTTP " \
        "status: 502 Bad Gateway" % mod.HAPROXY_IMAGE
    assert not [c for c in run.calls if c[1] == "run" and mod.HAPROXY_IMAGE in c]
    assert not (edge / "conf" / "haproxy.cfg").exists()


def test_a_failed_helper_pull_records_no_helper_user(tmp_path, monkeypatch):
    edge = tmp_path / "edge"
    edge.mkdir()
    monkeypatch.setattr(mod.os, "getuid", lambda: 1000)
    run = FakeRunner(daemon_id="rootful", unpulled={mod.ACME_IMAGE})
    with pytest.raises(mod.EdgeError, match="Could not pull %s" % re.escape(mod.ACME_IMAGE)):
        mod.helper_user(run, "docker", str(edge))
    assert not run.runs("touch")
    assert not (edge / ".helper-user").exists()


def test_a_render_error_names_the_instance(tmp_path):
    path = _instance(tmp_path, "one", hosts=("example.com:8443",))
    result = _sync(FakeRunner(), tmp_path / "edge", "one", path)
    assert re.search("Instance 'one'.*has port 8443, but HTTPS_PORT is 443", result["error"])


# --- the real images ---------------------------------------------------------- #

def _docker_ok():
    if not shutil.which("docker"):
        return False
    return subprocess.run(["docker", "info"], capture_output=True,
                          env=_DOCKER_ENV).returncode == 0


@pytest.mark.skipif(not _docker_ok(), reason="Docker is not available")
def test_certificates_and_validation_with_the_pinned_images(tmp_path):
    def run(argv, env=None):
        proc = subprocess.run(argv, capture_output=True, text=True,
                              env=dict(_DOCKER_ENV, **(env or {})))
        return proc.returncode, proc.stdout, proc.stderr

    # internal: acme would register a real Let's Encrypt account.
    path = _instance(tmp_path, "one", hosts=("example.com", "www.example.com"),
                     media=("images.example.com",),
                     env={"TLS_MODE": "internal"})
    edge = tmp_path / "edge"
    result = _sync(run, edge, "one", path)
    assert not (edge / "acme" / "thumbprint").exists()
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


def test_sync_holds_a_lock_beside_the_edge(tmp_path, monkeypatch):
    """Commands for two instances on one host can sync the edge at once."""
    path = _instance(tmp_path, "one")
    held = []
    real = mod._sync

    def spy(*args):
        lock = str(tmp_path / "edge.lock")
        with open(lock) as other:
            try:
                mod.fcntl.flock(other, mod.fcntl.LOCK_EX | mod.fcntl.LOCK_NB)
                held.append(False)
            except BlockingIOError:
                held.append(True)
        return real(*args)

    monkeypatch.setattr(mod, "_sync", spy)
    _sync(FakeRunner(), tmp_path / "edge", "one", path)
    assert held == [True]


# --- Let's Encrypt through acme.sh ------------------------------------------ #

def test_an_acme_instance_registers_the_account_and_answers_challenges(tmp_path):
    path = _instance(tmp_path, "one")
    edge = tmp_path / "edge"
    result = _sync(FakeRunner(), edge, "one", path)
    assert result["acme"]
    assert (edge / "acme" / "thumbprint").read_text() == "THUMB123\n"
    assert "THUMB123" in (edge / "conf" / "haproxy.cfg").read_text()
    compose = yaml.safe_load((edge / "docker-compose.yml").read_text())
    acme = compose["services"]["acme"]
    assert acme["container_name"] == mod.ACME_CONTAINER
    assert "./conf/certs:%s" % mod.CERT_DIR in acme["volumes"]


def test_the_account_is_registered_once(tmp_path):
    path = _instance(tmp_path, "one")
    edge = tmp_path / "edge"
    _sync(FakeRunner(), edge, "one", path)
    run = FakeRunner(running=True)
    _sync(run, edge, "one", path)
    assert not [c for c in run.calls if "--register-account" in c[-1]]


def test_an_internal_only_edge_never_contacts_lets_encrypt(tmp_path):
    path = _instance(tmp_path, "one", env={"TLS_MODE": "internal"})
    run = FakeRunner()
    result = _sync(run, tmp_path / "edge", "one", path)
    assert not result["acme"]
    assert not [c for c in run.calls if "--register-account" in c[-1]]
    assert "acme-challenge" not in (tmp_path / "edge" / "conf" / "haproxy.cfg").read_text()


def _issue(tmp_path, run, **instances):
    edge = tmp_path / "edge"
    for name, kwargs in instances.items():
        _sync(FakeRunner(running=True), edge, name, _instance(tmp_path, name, **kwargs))
    return mod.issue(run, "docker", str(edge))


def test_each_acme_hostname_is_issued_and_deployed(tmp_path):
    run = FakeRunner(running=True)
    result = _issue(tmp_path, run, one={"hosts": ("example.com",),
                                        "media": ("images.example.com",)})
    assert result["issued"] == ["example.com", "images.example.com"]
    issue = run.acme_calls("--issue")[0]
    assert issue[-6:] == ["--issue", "--stateless", "-d", "example.com",
                          "--server", "letsencrypt"]
    deploy = run.acme_calls("--deploy")[0]
    env = run.envs[run.calls.index(deploy)]
    assert "DEPLOY_HAPROXY_HOT_UPDATE" in deploy
    assert env["DEPLOY_HAPROXY_HOT_UPDATE"] == "yes"
    assert env["DEPLOY_HAPROXY_PEM_PATH"] == mod.CERT_DIR
    assert not result["reload"]


def test_staging_hostnames_use_the_staging_ca(tmp_path):
    run = FakeRunner(running=True)
    _issue(tmp_path, run, one={"env": {"CANASTA_STAGING_CERTS": "true"}})
    assert run.acme_calls("--issue")[0][-1] == "letsencrypt_test"


def test_an_issued_certificate_is_not_requested_again(tmp_path):
    run = FakeRunner(running=True, listed={"example.com": ("LetsEncrypt.org", True)})
    result = _issue(tmp_path, run, one={})
    assert result["issued"] == [] and run.acme_calls("--issue") == []


def test_leaving_staging_reissues_from_the_production_ca(tmp_path):
    run = FakeRunner(running=True,
                     listed={"example.com": ("LetsEncrypt.org_test", True)})
    result = _issue(tmp_path, run, one={})
    assert result["issued"] == ["example.com"]
    issue = run.acme_calls("--issue")[0]
    assert "--force" in issue and "letsencrypt" == issue[issue.index("--server") + 1]


def test_a_failed_first_issuance_is_retried(tmp_path):
    run = FakeRunner(running=True, listed={"example.com": ("LetsEncrypt.org", False)})
    result = _issue(tmp_path, run, one={})
    assert result["issued"] == ["example.com"]


class FailsOnce(FakeRunner):
    """Fails each hostname's first issuance as Let's Encrypt does when
    HAProxy answers its challenge with the HTTPS redirect."""

    def __init__(self, err="Verify error: Invalid response from http://example.com", **kwargs):
        super().__init__(**kwargs)
        self.err = err
        self.failed = set()

    def _acme(self, args):
        if args[0] == "--issue":
            host = args[args.index("-d") + 1]
            if host not in self.failed:
                self.failed.add(host)
                return 1, "", self.err
        return super()._acme(args)


def test_issuance_waits_for_haproxy_to_answer_challenges(tmp_path, monkeypatch):
    asked = []

    def answered(port, thumbprint):
        asked.append((port, thumbprint))
        return False
    run = FakeRunner(running=True)
    edge = tmp_path / "edge"
    _sync(FakeRunner(running=True), edge, "one",
          _instance(tmp_path, "one", env={"HTTP_PORT": "8080", "HTTPS_PORT": "8443"},
                    media=("images.example.com",)))
    monkeypatch.setattr(mod, "challenge_answered", answered)
    result = mod.issue(run, "docker", str(edge))
    assert run.acme_calls("--issue") == []
    assert [u["hostname"] for u in result["unissued"]] == ["example.com", "images.example.com"]
    assert "port 8080" in result["unissued"][0]["reason"]
    # Asked once per port, with the account's thumbprint.
    assert asked == [(8080, "THUMB123")]


def test_acme_dns_issuance_does_not_wait_for_challenges(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "challenge_answered", lambda port, thumbprint: 1 / 0)
    run = FakeRunner(running=True)
    result = _issue(tmp_path, run, one={"env": {"TLS_MODE": "acme-dns",
                                                "CLOUDFLARE_API_TOKEN": "t0k"}})
    assert result["issued"] == ["example.com"]


def test_a_failed_validation_is_retried_once_on_the_first_run(tmp_path, monkeypatch):
    asked = []
    monkeypatch.setattr(mod, "challenge_answered",
                        lambda port, thumbprint: asked.append(port) or True)
    run = FailsOnce(running=True)
    edge = tmp_path / "edge"
    _sync(FakeRunner(running=True), edge, "one", _instance(tmp_path, "one"))
    result = mod.issue(run, "docker", str(edge), retry=True)
    assert result["issued"] == ["example.com"] and result["unissued"] == []
    first, second = run.acme_calls("--issue")
    assert "--force" not in first and "--force" in second
    # HAProxy is asked again before the retry.
    assert asked == [80, 80]


def test_a_failed_validation_is_not_retried_after_the_first_run(tmp_path):
    run = FailsOnce(running=True)
    result = _issue(tmp_path, run, one={})
    assert len(run.acme_calls("--issue")) == 1
    assert result["unissued"][0]["hostname"] == "example.com"


def test_a_rate_limit_is_not_retried(tmp_path):
    run = FailsOnce(running=True, err=RATE_LIMITED)
    edge = tmp_path / "edge"
    _sync(FakeRunner(running=True), edge, "one", _instance(tmp_path, "one"))
    result = mod.issue(run, "docker", str(edge), retry=True)
    assert len(run.acme_calls("--issue")) == 1
    assert "retry after" in result["unissued"][0]["reason"]


def test_sync_reports_a_newly_registered_account(tmp_path):
    path = _instance(tmp_path, "one")
    assert _sync(FakeRunner(), tmp_path / "edge", "one", path)["acme_registered"]
    assert not _sync(FakeRunner(), tmp_path / "edge", "one", path)["acme_registered"]


def _serve(handler):
    import http.server
    import threading
    server = http.server.HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_challenge_answered_reads_the_thumbprint_from_the_port(monkeypatch):
    import http.server
    monkeypatch.setattr(mod, "CHALLENGE_WAIT", 0)

    class Rule(http.server.BaseHTTPRequestHandler):
        thumbprint = "THUMB"

        def do_GET(self):
            body = ("%s.%s\n" % (self.path.rsplit("/", 1)[1], self.thumbprint)).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    class Redirect(Rule):
        def do_GET(self):
            self.send_response(301)
            self.send_header("Location", "https://example.com/")
            self.send_header("Content-Length", "0")
            self.end_headers()

    for handler, expected in ((Rule, True), (Redirect, False)):
        server = _serve(handler)
        try:
            assert _CHALLENGE_ANSWERED(server.server_port, "THUMB") is expected
            if expected:
                assert not _CHALLENGE_ANSWERED(server.server_port, "OTHER")
        finally:
            server.shutdown()
            server.server_close()


def test_a_failure_is_reported_without_failing(tmp_path):
    run = FakeRunner(running=True, issue_fails={"example.com"})
    result = _issue(tmp_path, run, one={})
    assert result["issued"] == []
    assert result["unissued"][0]["hostname"] == "example.com"
    assert "Invalid response" in result["unissued"][0]["reason"]
    assert run.acme_calls("--deploy") == []


# acme.sh 3.1.6's stderr for a refused order: Let's Encrypt's problem
# document as it sent it, then acme.sh's closing lines.
RATE_LIMITED = (
    "[Sun Oct 18 21:35:44 UTC 2026] Error creating new order. "
    "Le_OrderFinalize not found. {\n"
    '  "type": "urn:ietf:params:acme:error:rateLimited",\n'
    '  "detail": "too many certificates (5) already issued for this exact set '
    "of identifiers in the last 168h0m0s, retry after 2026-10-20 14:02:11 UTC: "
    "see https://letsencrypt.org/docs/rate-limits/"
    '#new-certificates-per-exact-set-of-identifiers",\n'
    '  "status": 429\n'
    "}\n"
    "[Sun Oct 18 21:35:45 UTC 2026] Please add '--debug' or '--log' to see more information.\n"
    "[Sun Oct 18 21:35:45 UTC 2026] See: https://github.com/acmesh-official/acme.sh/wiki/How-to-debug-acme.sh\n"
)

# A failed challenge, which acme.sh reports by its detail alone.
CHALLENGE_FAILED = (
    "[Sun Oct 18 21:35:44 UTC 2026] example.com: Invalid status. Verification "
    "error details: 203.0.113.7: Fetching http://example.com/.well-known/"
    "acme-challenge/abc: Timeout during connect (likely firewall problem)\n"
    "[Sun Oct 18 21:35:45 UTC 2026] Please add '--debug' or '--log' to see more information.\n"
    "[Sun Oct 18 21:35:45 UTC 2026] See: https://github.com/acmesh-official/acme.sh/wiki/How-to-debug-acme.sh\n"
)


def test_a_problem_document_gives_its_type_and_detail():
    kind, reason = mod.acme_failure("", RATE_LIMITED)
    assert kind == "rateLimited"
    assert reason.startswith("too many certificates (5) already issued")
    assert reason.endswith("#new-certificates-per-exact-set-of-identifiers")


def test_a_compact_problem_document_is_read_too():
    err = ('[date] example.com: Invalid status, Verification error: "error":'
           '{"type":"urn:ietf:params:acme:error:caa","detail":"CAA record for '
           'example.com prevents issuance","status":403\n')
    assert mod.acme_failure("", err) == ("caa", "CAA record for example.com prevents issuance")


def test_without_a_problem_document_the_last_line_before_the_closing_ones_is_used():
    kind, reason = mod.acme_failure("", CHALLENGE_FAILED)
    assert kind is None
    assert reason.startswith("example.com: Invalid status. Verification error details:")
    assert "Timeout during connect" in reason


def test_output_of_only_closing_lines_gives_no_reason():
    closing = "\n".join(CHALLENGE_FAILED.splitlines()[1:])
    assert mod.acme_failure("", closing) == (None, "")


def test_hints_follow_the_problem_type():
    assert mod.acme_hint("rateLimited", "example.com") == ""
    assert "CAA" in mod.acme_hint("caa", "example.com")
    assert mod.acme_hint("dns", "example.com") == "Check that example.com has a DNS record."
    for kind in (None, "connection", "unauthorized"):
        assert "port 80" in mod.acme_hint(kind, "example.com")


def test_a_rate_limit_is_reported_with_lets_encrypts_detail(tmp_path):
    class RateLimited(FakeRunner):
        def _acme(self, args):
            if args[0] == "--issue":
                return 1, "", RATE_LIMITED
            return super()._acme(args)

    result = _issue(tmp_path, RateLimited(running=True), one={})
    [failure] = result["unissued"]
    assert failure["hostname"] == "example.com"
    assert "retry after 2026-10-20" in failure["reason"]
    assert "See:" not in failure["reason"]
    assert failure["hint"] == ""


def test_a_hostname_gone_from_lets_encrypt_is_removed_and_replaced(tmp_path):
    """A hostname switched to internal keeps being served, but from the
    edge's CA: its Let's Encrypt PEM is replaced and HAProxy reloaded."""
    run = FakeRunner(running=True, listed={"example.com": ("LetsEncrypt.org", True),
                                           "old.example.org": ("LetsEncrypt.org", True)})
    result = _issue(tmp_path, run, one={"env": {"TLS_MODE": "internal"}})
    assert result["removed"] == ["example.com", "old.example.org"]
    assert result["reload"]
    assert run.runs("openssl")


def test_results_avoid_keys_ansible_reserves(tmp_path):
    """Ansible reads failed, skipped and the like on a registered result as
    its own, so a module result must not use them for data."""
    reserved = {"failed", "skipped", "unreachable", "rc", "msg", "results"}
    path = _instance(tmp_path, "one")
    synced = _sync(FakeRunner(), tmp_path / "edge", "one", path)
    issued = mod.issue(FakeRunner(running=True, issue_fails={"example.com"}),
                       "docker", str(tmp_path / "edge"))
    assert not reserved & (set(synced) | set(issued))


class OwnerProbe(FakeRunner):
    """Creates the probe file, owned as a rootful or rootless runtime would."""

    def __init__(self, owner_uid, **kwargs):
        super().__init__(**kwargs)
        self.owner_uid = owner_uid

    def __call__(self, argv, env=None, cwd=None):
        if "touch" in argv:
            mounts = dict(a.split(":", 1)[::-1] for a in argv if ":/" in a)
            path = os.path.join(mounts["/probe"], ".owner-probe")
            open(path, "w").close()
            self.created = path
        return super().__call__(argv, env)


def test_helpers_run_as_the_operator_where_container_root_is_host_root(tmp_path, monkeypatch):
    edge = tmp_path / "edge"
    edge.mkdir()
    real_stat = os.stat
    monkeypatch.setattr(mod.os, "getuid", lambda: 1000)
    monkeypatch.setattr(mod.os, "getgid", lambda: 1000)
    monkeypatch.setattr(mod.os, "stat", lambda p, *a, **k: type(
        "S", (), {"st_uid": 0})() if p.endswith(".owner-probe") else real_stat(p, *a, **k))
    assert mod.helper_user(OwnerProbe(0, daemon_id="rootful"), "docker", str(edge)) == "1000:1000"
    assert (edge / ".helper-user").read_text() == "1000:1000\n"
    assert (edge / ".helper-user-daemon").read_text() == "rootful\n1000\n"
    assert mod.helper_user(FakeRunner(daemon_id="rootful"), "docker", str(edge)) == "1000:1000"


def test_helpers_decide_again_on_another_daemon(tmp_path, monkeypatch):
    edge = tmp_path / "edge"
    edge.mkdir()
    (edge / ".helper-user").write_text("1000:1000\n")
    (edge / ".helper-user-daemon").write_text("rootful\n1000\n")
    monkeypatch.setattr(mod.os, "getuid", lambda: 1000)
    real_stat = os.stat
    monkeypatch.setattr(mod.os, "stat", lambda p, *a, **k: type(
        "S", (), {"st_uid": 1000})() if p.endswith(".owner-probe") else real_stat(p, *a, **k))
    run = OwnerProbe(1000, daemon_id="rootless")
    assert mod.helper_user(run, "docker", str(edge)) == "root"
    assert run.runs("touch")
    assert (edge / ".helper-user").read_text() == "root\n"
    assert (edge / ".helper-user-daemon").read_text() == "rootless\n1000\n"


def test_helpers_decide_again_for_another_user(tmp_path, monkeypatch):
    edge = tmp_path / "edge"
    edge.mkdir()
    (edge / ".helper-user").write_text("root\n")
    (edge / ".helper-user-daemon").write_text("rootful\n0\n")
    monkeypatch.setattr(mod.os, "getuid", lambda: 1000)
    monkeypatch.setattr(mod.os, "getgid", lambda: 1000)
    real_stat = os.stat
    monkeypatch.setattr(mod.os, "stat", lambda p, *a, **k: type(
        "S", (), {"st_uid": 0})() if str(p).endswith(".owner-probe") else real_stat(p, *a, **k))
    run = OwnerProbe(0, daemon_id="rootful")
    assert mod.helper_user(run, "docker", str(edge)) == "1000:1000"
    assert run.runs("touch")
    assert (edge / ".helper-user-daemon").read_text() == "rootful\n1000\n"


def test_helpers_decide_again_over_a_cache_without_a_uid(tmp_path, monkeypatch):
    edge = tmp_path / "edge"
    edge.mkdir()
    (edge / ".helper-user").write_text("root\n")
    (edge / ".helper-user-daemon").write_text("rootful\n")
    monkeypatch.setattr(mod.os, "getuid", lambda: 1000)
    monkeypatch.setattr(mod.os, "getgid", lambda: 1000)
    real_stat = os.stat
    monkeypatch.setattr(mod.os, "stat", lambda p, *a, **k: type(
        "S", (), {"st_uid": 0})() if str(p).endswith(".owner-probe") else real_stat(p, *a, **k))
    assert mod.helper_user(OwnerProbe(0, daemon_id="rootful"), "docker", str(edge)) == "1000:1000"


# --- whether this run can write the edge ---------------------------------------- #

def _owned_elsewhere(monkeypatch):
    """Run as a uid that owns nothing here, so only the write bits decide."""
    uid = os.getuid() + 4242
    monkeypatch.setattr(mod.os, "getuid", lambda: uid)


@pytest.mark.skipif(os.getuid() == 0, reason="root can write anything")
def test_the_check_names_files_this_user_cannot_write(tmp_path, monkeypatch):
    path = _instance(tmp_path, "one")
    edge = tmp_path / "config" / "edge"
    (edge / "conf" / "certs").mkdir(parents=True)
    pem = edge / "conf" / "certs" / "example.com.pem"
    pem.write_text("x")
    (tmp_path / "config" / "edge.lock").write_text("")
    layer = tmp_path / "one" / mod.LAYER_FILE
    layer.write_text("x")
    _owned_elsewhere(monkeypatch)
    assert mod.unwritable_paths(str(edge), path) == []
    for p in (pem, layer, tmp_path / "config" / "edge.lock"):
        p.chmod(0o444)
    try:
        assert mod.unwritable_paths(str(edge), path) == [
            str(tmp_path / "config" / "edge.lock"), str(pem), str(layer)]
        with pytest.raises(mod.EdgeError, match=re.escape(str(pem))):
            mod.check_writable(str(edge), path)
    finally:
        for p in (pem, layer, tmp_path / "config" / "edge.lock"):
            p.chmod(0o644)


@pytest.mark.skipif(os.getuid() == 0, reason="root can write anything")
def test_the_check_needs_the_config_dir_for_a_new_lock(tmp_path, monkeypatch):
    config = tmp_path / "config"
    config.mkdir()
    _owned_elsewhere(monkeypatch)
    config.chmod(0o555)
    try:
        assert mod.unwritable_paths(str(config / "edge")) == [str(config)]
    finally:
        config.chmod(0o755)


@pytest.mark.skipif(os.getuid() == 0, reason="needs an edge owned by another user")
def test_root_is_refused_an_edge_another_user_owns(tmp_path, monkeypatch):
    edge = tmp_path / "config" / "edge"
    edge.mkdir(parents=True)
    owner = mod._user_name(os.getuid())
    monkeypatch.setattr(mod.os, "getuid", lambda: 0)
    assert mod.foreign_owner(str(edge)) == owner
    with pytest.raises(mod.EdgeError, match="running as root.*belongs to %s" % re.escape(owner)):
        mod.check_writable(str(edge))
    # Before the edge exists, the directory it goes in decides.
    assert mod.foreign_owner(str(tmp_path / "config" / "other")) == owner


def test_the_operator_is_not_refused_their_own_edge(tmp_path):
    edge = tmp_path / "config" / "edge"
    edge.mkdir(parents=True)
    if os.getuid() != 0:
        assert mod.foreign_owner(str(edge)) is None
    mod.check_writable(str(edge), _instance(tmp_path, "one"))


def test_helpers_decide_again_over_a_cache_without_a_daemon(tmp_path, monkeypatch):
    edge = tmp_path / "edge"
    edge.mkdir()
    (edge / ".helper-user").write_text("1000:1000\n")
    monkeypatch.setattr(mod.os, "getuid", lambda: 1000)
    real_stat = os.stat
    monkeypatch.setattr(mod.os, "stat", lambda p, *a, **k: type(
        "S", (), {"st_uid": 1000})() if p.endswith(".owner-probe") else real_stat(p, *a, **k))
    assert mod.helper_user(OwnerProbe(1000, daemon_id="rootless"), "docker", str(edge)) == "root"


def test_helpers_stay_root_on_a_rootless_runtime(tmp_path, monkeypatch):
    edge = tmp_path / "edge"
    edge.mkdir()
    uid = os.getuid() or 1000
    monkeypatch.setattr(mod.os, "getuid", lambda: uid)
    real_stat = os.stat
    monkeypatch.setattr(mod.os, "stat", lambda p, *a, **k: type(
        "S", (), {"st_uid": uid})() if p.endswith(".owner-probe") else real_stat(p, *a, **k))
    assert mod.helper_user(OwnerProbe(uid), "podman", str(edge)) == "root"


# --- acme-dns ------------------------------------------------------------------ #

DNS_ENV = {"TLS_MODE": "acme-dns", "CLOUDFLARE_API_TOKEN": "tok-secret"}


def test_acme_dns_issues_through_the_cloudflare_hook(tmp_path):
    run = FakeRunner(running=True)
    result = _issue(tmp_path, run, one={"env": DNS_ENV})
    assert result["issued"] == ["example.com"]
    issue = run.acme_calls("--issue")[0]
    assert issue[issue.index("--issue"):issue.index("--server")] == [
        "--issue", "--dns", "dns_cf", "-d", "example.com"]
    assert run.envs[run.calls.index(issue)] == {"CF_Token": "tok-secret"}


def test_the_token_never_reaches_the_command_line(tmp_path):
    run = FakeRunner(running=True)
    _issue(tmp_path, run, one={"env": dict(DNS_ENV, CLOUDFLARE_ZONE_ID="z1")})
    assert not [c for c in run.calls if any("tok-secret" in a for a in c)]
    issue = run.acme_calls("--issue")[0]
    assert run.envs[run.calls.index(issue)] == {"CF_Token": "tok-secret",
                                                "CF_Zone_ID": "z1"}


def test_switching_validation_method_reissues(tmp_path):
    """acme.sh renews by the method it issued with."""
    run = FakeRunner(running=True, listed={"example.com": ("LetsEncrypt.org", True)})
    edge = tmp_path / "edge"
    conf = edge / "acme" / "example.com_ecc"
    conf.mkdir(parents=True)
    (conf / "example.com.conf").write_text("Le_Webroot='stateless'\n")
    result = _issue(tmp_path, run, one={"env": DNS_ENV})
    assert result["issued"] == ["example.com"]
    assert "--force" in run.acme_calls("--issue")[0]


def test_acme_dns_needs_a_token(tmp_path):
    path = _instance(tmp_path, "one", env={"TLS_MODE": "acme-dns"})
    assert re.search("CLOUDFLARE_API_TOKEN is not set", _sync(FakeRunner(), tmp_path / "edge", "one", path)["error"])


def test_different_tokens_without_zone_ids_are_refused(tmp_path):
    edge = tmp_path / "edge"
    one = _instance(tmp_path, "one", env=DNS_ENV)
    two = _instance(tmp_path, "two", hosts=("two.example.org",),
                    env=dict(DNS_ENV, CLOUDFLARE_API_TOKEN="other"))
    _sync(FakeRunner(), edge, "one", one)
    assert re.search("Set CLOUDFLARE_ZONE_ID for each", _sync(FakeRunner(), edge, "two", two)["error"])


def test_different_tokens_with_zone_ids_are_fine(tmp_path):
    edge = tmp_path / "edge"
    one = _instance(tmp_path, "one", env=dict(DNS_ENV, CLOUDFLARE_ZONE_ID="z1"))
    two = _instance(tmp_path, "two", hosts=("two.example.org",),
                    env=dict(DNS_ENV, CLOUDFLARE_API_TOKEN="other",
                             CLOUDFLARE_ZONE_ID="z2"))
    _sync(FakeRunner(), edge, "one", one)
    assert _sync(FakeRunner(), edge, "two", two)["members"] == ["one", "two"]


# --- mitigation inputs -------------------------------------------------------- #

def test_deny_lists_are_read_from_the_instance(tmp_path):
    path = _instance(tmp_path, "one")
    with open(os.path.join(path, "config", "edge-deny-user-agents.list"), "w") as f:
        f.write("# crawlers\nGPTBot\n")
    with open(os.path.join(path, "config", "edge-deny-cidrs.list"), "w") as f:
        f.write("203.0.113.0/24\n")
    edge = tmp_path / "edge"
    _sync(FakeRunner(), edge, "one", path)
    assert (edge / "conf" / "deny-ua-one.lst").read_text() == "GPTBot\n"
    assert (edge / "conf" / "deny-cidr-one.lst").read_text() == "203.0.113.0/24\n"
    os.remove(os.path.join(path, "config", "edge-deny-user-agents.list"))
    _sync(FakeRunner(running=True), edge, "one", path)
    assert not (edge / "conf" / "deny-ua-one.lst").exists()


def test_cdn_ranges_are_fetched(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "_fetch", lambda url: "192.0.2.0/24" if "v4" in url
                        else "2001:db8::/32")
    path = _instance(tmp_path, "one", env={"TRUSTED_PROXIES": "cloudflare"})
    _sync(FakeRunner(), tmp_path / "edge", "one", path)
    assert (tmp_path / "edge" / "conf" / "cdn-cloudflare.lst").read_text() == (
        "192.0.2.0/24\n2001:db8::/32\n")


def test_unreachable_cdn_ranges_fall_back_to_the_last_copy(tmp_path, monkeypatch):
    edge = tmp_path / "edge"
    (edge / "conf").mkdir(parents=True)
    (edge / "conf" / "cdn-cloudflare.lst").write_text("192.0.2.0/24\n")
    monkeypatch.setattr(mod, "_fetch", lambda url: None)
    assert mod.cdn_ranges(str(edge), ["cloudflare"]) == {"cloudflare": ["192.0.2.0/24"]}


def test_without_a_copy_the_bundled_ranges_are_used(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "_fetch", lambda url: "<html>not a list</html>")
    ranges = mod.cdn_ranges(str(tmp_path), ["imperva"])["imperva"]
    assert ranges == mod.BUNDLED_CDN_RANGES["imperva"]


# --- Caddyfile.global sites ---------------------------------------------------- #

def test_caddyfile_global_sites_are_routed_and_certified(tmp_path):
    path = _instance(tmp_path, "one", env={"TLS_MODE": "internal"})
    with open(os.path.join(path, "config", "Caddyfile.global"), "w") as f:
        f.write("{\n    email a@example.com\n}\n"
                "www.example.com {\n    redir https://example.com{uri}\n}\n"
                "*.example.org {\n    respond x\n}\n")
    edge = tmp_path / "edge"
    result = _sync(FakeRunner(), edge, "one", path)
    assert "www.example.com@443 one/wiki" in (edge / "conf" / "hosts.map").read_text()
    assert "www.example.com.pem" in os.listdir(edge / "conf" / "certs")
    assert result["unrouted_sites"] == [
        {"instance": "one", "address": "*.example.org", "reason": "it is a wildcard"}]


def test_an_overlapping_sitemap_path_is_left_out_and_reported(tmp_path):
    path = _instance(tmp_path, "one", hosts=("example.com", "example.com/docs"),
                     env={"MW_SITEMAP_URL_PATH": "/docs/sitemap"})
    edge = tmp_path / "edge"
    result = _sync(FakeRunner(), edge, "one", path)
    assert "error" not in result, result
    assert "{ path_beg /docs/docs/sitemap/ }" in (edge / "conf" / "haproxy.cfg").read_text()
    assert result["sitemap_overlaps"] == [
        "MW_SITEMAP_URL_PATH=/docs/sitemap: the sitemaps of wiki 'w0' are not served "
        "at example.com/docs/sitemap, which is inside the URL of wiki 'w1' "
        "(example.com/docs)."]


# --- moving existing instances behind the edge -------------------------------- #

def _plain(root, name, env=None, hosts=("example.com",)):
    """An instance as the previous release left it: no edge layer."""
    return _instance(root, name, hosts=hosts, env=env)


def _behind(path):
    mod.write_layer(path, os.path.basename(path), True)
    return path


def _entry(name, path, runtime="docker compose"):
    return {"id": name, "path": path, "compose_command": runtime}


def test_an_instance_on_the_standard_ports_moves(tmp_path):
    one = _plain(tmp_path, "one")
    assert mod.plan_conversion([_entry("one", one)]) == {
        "convert": ["one"], "rootless": [], "rated": [], "refuse": None, "held": []}


@pytest.mark.parametrize("env,hosts", [
    ({"TLS_MODE": "off"}, ("example.com",)),
    ({"HTTP_PORT": "8080", "HTTPS_PORT": "8443"}, ("example.com:8443",)),
    ({}, ("localhost",)),
    ({}, ("192.0.2.4",)),
])
def test_plain_own_port_and_unnamed_instances_move(tmp_path, env, hosts):
    one = _plain(tmp_path, "one", env=env, hosts=hosts)
    assert mod.plan_conversion([_entry("one", one)])["convert"] == ["one"]


def test_instances_already_behind_the_edge_are_left_alone(tmp_path):
    one = _behind(_plain(tmp_path, "one"))
    two = _plain(tmp_path, "two", hosts=("two.example.org",))
    plan = mod.plan_conversion([_entry("one", one), _entry("two", two)])
    assert plan["convert"] == ["two"] and plan["refuse"] is None


@pytest.mark.parametrize("hosts,fragment", [
    (("*.example.com",), "is a wildcard"),
    (("exa_mple.com",), "is not a valid hostname"),
])
def test_an_instance_the_edge_cannot_serve_refuses_the_host(tmp_path, hosts, fragment):
    one = _plain(tmp_path, "one", hosts=hosts)
    good = _plain(tmp_path, "good", hosts=("good.example.org",))
    plan = mod.plan_conversion([_entry("one", one), _entry("good", good)])
    assert plan["convert"] == [] and fragment in plan["refuse"]
    assert sorted(plan["held"]) == ["good", "one"]


def test_instances_that_clash_on_a_port_refuse_the_host(tmp_path):
    one = _plain(tmp_path, "one", hosts=("localhost",))
    two = _plain(tmp_path, "two", hosts=("192.0.2.4",))
    plan = mod.plan_conversion([_entry("one", one), _entry("two", two)])
    assert plan["convert"] == [] and "cannot tell apart" in plan["refuse"]


def test_an_instance_on_other_ports_moves_too(tmp_path):
    one = _plain(tmp_path, "one")
    other = _plain(tmp_path, "other", env={"HTTP_PORT": "8080", "HTTPS_PORT": "8443"},
                   hosts=("other.example.org:8443",))
    plan = mod.plan_conversion([_entry("one", one), _entry("other", other)])
    assert sorted(plan["convert"]) == ["one", "other"]


def test_a_host_mixing_docker_and_podman_is_refused(tmp_path):
    one = _plain(tmp_path, "one")
    two = _plain(tmp_path, "two", hosts=("two.example.org",))
    plan = mod.plan_conversion([_entry("one", one), _entry("two", two, "podman-compose")])
    assert plan["convert"] == [] and "both Docker and Podman" in plan["refuse"]
    assert "'one'" in plan["refuse"] and "'two'" in plan["refuse"]


def test_a_host_mixing_rootful_and_rootless_docker_is_refused(tmp_path):
    one = _plain(tmp_path, "one")
    two = dict(_entry("two", _plain(tmp_path, "two", hosts=("two.example.org",))),
               docker_host="unix:///run/user/1000/docker.sock")
    plan = mod.plan_conversion([_entry("one", one), two],
                               rootless=lambda inst: bool(inst.get("docker_host")))
    assert plan["convert"] == []
    assert "both rootful and rootless Docker" in plan["refuse"]
    assert "'one'" in plan["refuse"] and "'two'" in plan["refuse"]


def test_a_host_all_on_rootless_docker_moves(tmp_path):
    one = _plain(tmp_path, "one")
    two = _plain(tmp_path, "two", hosts=("two.example.org",))
    plan = mod.plan_conversion([_entry("one", one), _entry("two", two)],
                               rootless=lambda inst: True)
    assert sorted(plan["convert"]) == ["one", "two"] and plan["refuse"] is None


def test_two_docker_compose_commands_are_one_runtime(tmp_path):
    one = _plain(tmp_path, "one")
    two = _plain(tmp_path, "two", hosts=("two.example.org",))
    plan = mod.plan_conversion([_entry("one", one), _entry("two", two, "docker-compose")])
    assert sorted(plan["convert"]) == ["one", "two"] and plan["refuse"] is None


def test_podman_instances_move_when_systemd_can_run_the_edge(tmp_path):
    one = _plain(tmp_path, "one")
    # Podman reports itself rootless as well; that is not rootless Docker.
    plan = mod.plan_conversion([_entry("one", one, "podman-compose")],
                               rootless=lambda inst: True, podman_ready=lambda: True)
    assert plan == {"convert": ["one"], "rootless": [], "rated": [], "refuse": None,
                    "held": []}


def test_podman_without_a_systemd_user_session_refuses_the_host(tmp_path):
    one = _plain(tmp_path, "one")
    plan = mod.plan_conversion([_entry("one", one, "podman-compose")], podman_ready=lambda: False)
    assert plan["convert"] == [] and "systemd user session" in plan["refuse"]


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write anywhere")
def test_an_instance_the_cli_cannot_write_refuses_the_host(tmp_path):
    one = _plain(tmp_path, "one")
    os.chmod(os.path.join(one, ".env"), 0o444)
    try:
        plan = mod.plan_conversion([_entry("one", one)])
    finally:
        os.chmod(os.path.join(one, ".env"), 0o644)
    assert plan["convert"] == [] and "cannot write to its directory" in plan["refuse"]


def test_rootless_docker_instances_move_and_are_listed(tmp_path):
    one = _plain(tmp_path, "one")
    entry = dict(_entry("one", one), docker_host="unix:///run/user/1000/docker.sock")
    plan = mod.plan_conversion([entry], lambda inst: inst["docker_host"].startswith("unix:///run/user/"))
    assert plan["convert"] == ["one"] and plan["rootless"] == ["one"]
    assert plan["rated"] == []


def test_rootless_docker_instances_that_set_the_rates_are_listed(tmp_path):
    one = _plain(tmp_path, "one")
    with open(os.path.join(one, ".env"), "a") as f:
        f.write("CANASTA_EDGE_RATE_DYNAMIC=40\n")
    plan = mod.plan_conversion([_entry("one", one)], lambda inst: True)
    assert plan["rootless"] == ["one"] and plan["rated"] == ["one"]


LEAF = "-----BEGIN CERTIFICATE-----\nLEAF\n-----END CERTIFICATE-----\n"
CHAIN = LEAF + "-----BEGIN CERTIFICATE-----\nINTERMEDIATE\n-----END CERTIFICATE-----\n"
EC_KEY = "-----BEGIN EC PRIVATE KEY-----\nKEY\n-----END EC PRIVATE KEY-----\n"


class CaddyVolume(FakeRunner):
    """Serves a caddy-data certificates tree through `compose run`, and
    certificate dates through `openssl`."""

    def __init__(self, files, **kwargs):
        super().__init__(**kwargs)
        self.files = files

    def __call__(self, argv, env=None, cwd=None, data=None):
        if "run" in argv and "caddy" in argv:
            import base64
            import io
            import tarfile
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w") as archive:
                for name, content in self.files.items():
                    info = tarfile.TarInfo("./" + name)
                    info.size = len(content.encode())
                    archive.addfile(info, io.BytesIO(content.encode()))
            return 0, base64.b64encode(buf.getvalue()).decode(), ""
        if "x509" in argv:
            return 0, ("notBefore=Oct 17 03:27:34 2026 GMT\n"
                       "notAfter=Jan 15 03:27:33 2027 GMT\n"), ""
        return super().__call__(argv, env)


LE = "acme-v02.api.letsencrypt.org-directory"


def _import(tmp_path, files, env=None, hosts=("example.com",)):
    path = _instance(tmp_path, "one", hosts=hosts, env=env)
    edge = tmp_path / "edge"
    run = CaddyVolume(files)
    return path, edge, mod.import_caddy_certificates(
        run, "docker", str(edge), "one", path, "docker compose")


def test_caddy_certificates_become_edge_pems_and_acme_records(tmp_path):
    _, edge, imported = _import(tmp_path, {
        "%s/example.com/example.com.crt" % LE: CHAIN,
        "%s/example.com/example.com.key" % LE: EC_KEY,
    })
    assert imported == ["example.com"]
    assert (edge / "conf" / "certs" / "example.com.pem").read_text() == CHAIN + EC_KEY
    record = edge / "acme" / "example.com_ecc"
    assert (record / "example.com.cer").read_text() == LEAF
    assert "INTERMEDIATE" in (record / "ca.cer").read_text()
    conf = (record / "example.com.conf").read_text()
    assert "Le_API='https://acme-v02.api.letsencrypt.org/directory'" in conf
    assert "Le_Webroot='stateless'" in conf and "Le_Keylength='ec-256'" in conf
    # Renewal 30 days before notAfter (2027-01-15 03:27:33 UTC).
    assert "Le_NextRenewTimeStr='2026-12-16T03:27:33Z'" in conf


def test_rsa_and_internal_certificates_are_not_imported(tmp_path):
    rsa = "-----BEGIN RSA PRIVATE KEY-----\nK\n-----END RSA PRIVATE KEY-----\n"
    _, edge, imported = _import(tmp_path, {
        "%s/example.com/example.com.crt" % LE: CHAIN,
        "%s/example.com/example.com.key" % LE: rsa,
        "local/www.example.com/www.example.com.crt": CHAIN,
        "local/www.example.com/www.example.com.key": EC_KEY,
    }, hosts=("example.com", "www.example.com"))
    assert imported == []


def test_a_staging_instance_takes_staging_certificates(tmp_path):
    stg = "acme-staging-v02.api.letsencrypt.org-directory"
    _, edge, imported = _import(tmp_path, {
        "%s/example.com/example.com.crt" % LE: CHAIN,
        "%s/example.com/example.com.key" % LE: EC_KEY,
        "%s/example.com/example.com.crt" % stg: CHAIN,
        "%s/example.com/example.com.key" % stg: EC_KEY,
    }, env={"CANASTA_STAGING_CERTS": "true"})
    conf = (edge / "acme" / "example.com_ecc" / "example.com.conf").read_text()
    assert imported == ["example.com"] and "acme-staging-v02" in conf


def test_an_imported_certificate_is_deployed_not_reissued(tmp_path):
    """acme.sh lists an imported record as issued; the edge deploys it once
    so acme.sh records its deploy hook for renewals."""
    edge = tmp_path / "edge"
    record = edge / "acme" / "example.com_ecc"
    record.mkdir(parents=True)
    (record / "example.com.conf").write_text("Le_Webroot='stateless'\n")
    run = FakeRunner(running=True, listed={"example.com": ("LetsEncrypt.org", True)})
    _issue(tmp_path, run, one={})
    assert run.acme_calls("--issue") == []
    assert len(run.acme_calls("--deploy")) == 1


def _imported(tmp_path, *hosts):
    for host in hosts:
        record = tmp_path / "edge" / "acme" / ("%s_ecc" % host)
        record.mkdir(parents=True)
        (record / ("%s.conf" % host)).write_text("Le_Webroot='stateless'\n")
    return {h: ("LetsEncrypt.org", True) for h in hosts}


def test_deploys_wait_for_a_starting_haproxy(tmp_path, monkeypatch):
    # The upgrade starts the edge and deploys the certificates imported from
    # Caddy at once; HAProxy's stats socket does not answer for a moment.
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    run = FakeRunner(running=True, listed=_imported(tmp_path, "example.com"), stats_down=2)
    result = _issue(tmp_path, run, one={})
    assert len(run.acme_calls("--deploy")) == 1
    assert result["unregistered"] == [] and result["unissued"] == []
    assert sum("socat" in c[-1] for c in run.calls if c[1] == "exec") == 3


def test_a_silent_haproxy_leaves_imports_unregistered_not_unissued(tmp_path, monkeypatch):
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    monkeypatch.setattr(mod, "STATS_SOCKET_WAIT", 0)
    run = FakeRunner(running=True, listed=_imported(tmp_path, "example.com"), stats_down=99)
    result = _issue(tmp_path, run, one={})
    assert run.acme_calls("--deploy") == []
    assert result["unissued"] == []
    assert result["unregistered"] == [
        {"hostname": "example.com", "reason": "HAProxy's stats socket did not answer"}]


def _deployed_before(tmp_path, host="example.com", staging=False):
    """acme.sh's record of a certificate it issued and deployed, kept in
    acme/ while conf/ was removed with the last instance."""
    record = tmp_path / "edge" / "acme" / ("%s_ecc" % host)
    record.mkdir(parents=True)
    (record / ("%s.conf" % host)).write_text(
        "Le_Webroot='stateless'\nLe_DeployHook='haproxy,'\n")
    (record / ("%s.cer" % host)).write_text(LEAF)
    (record / "fullchain.cer").write_text(CHAIN)
    return {host: (mod.ACME_CA_NAMES[staging], True)}


@pytest.mark.parametrize("staging", [False, True])
def test_a_cached_certificate_replaces_the_edge_ca_stand_in(tmp_path, staging):
    env = {"CANASTA_STAGING_CERTS": "true"} if staging else {}
    run = FakeRunner(running=True, listed=_deployed_before(tmp_path, staging=staging))
    result = _issue(tmp_path, run, one={"env": env})
    assert run.acme_calls("--issue") == []
    deploy = run.acme_calls("--deploy")
    assert len(deploy) == 1 and deploy[0][deploy[0].index("-d") + 1] == "example.com"
    assert result["changed"] and result["uninstalled"] == []


def test_a_cached_certificate_already_served_is_not_deployed_again(tmp_path):
    run = FakeRunner(running=True, listed=_deployed_before(tmp_path))
    edge = tmp_path / "edge"
    _sync(FakeRunner(running=True), edge, "one", _instance(tmp_path, "one"))
    # As acme.sh's HAProxy hook writes it: no blank lines, chain then key.
    (edge / "conf" / "certs" / "example.com.pem").write_text(
        (CHAIN + EC_KEY).replace("\n", "\r\n"))
    result = mod.issue(run, "docker", str(edge))
    assert run.acme_calls("--deploy") == [] and run.acme_calls("--issue") == []
    assert not result["changed"]


def test_a_silent_haproxy_leaves_a_cached_certificate_uninstalled(tmp_path, monkeypatch):
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    monkeypatch.setattr(mod, "STATS_SOCKET_WAIT", 0)
    run = FakeRunner(running=True, listed=_deployed_before(tmp_path), stats_down=99)
    result = _issue(tmp_path, run, one={})
    assert run.acme_calls("--deploy") == []
    assert result["unissued"] == [] and result["unregistered"] == []
    assert result["uninstalled"] == [
        {"hostname": "example.com", "reason": "HAProxy's stats socket did not answer"}]


def test_a_custom_hostname_beside_a_cached_one_is_left_alone(tmp_path):
    run = FakeRunner(running=True, listed=_deployed_before(tmp_path))
    _deployed_before(tmp_path, host="custom.example.com")
    _issue(tmp_path, run, one={
        "hosts": ("example.com", "custom.example.com"),
        "env": {"TLS_MODE": "custom", "TLS_MODE_HOSTS": "example.com=acme",
                "TLS_CERT": "c.pem", "TLS_KEY": "k.pem"},
        "certs": {"c.pem": LEAF, "k.pem": EC_KEY}})
    hosts = [c[c.index("-d") + 1] for c in run.acme_calls("--deploy")]
    assert hosts == ["example.com"]


def test_kubernetes_instances_are_left_out_of_the_plan(tmp_path):
    one = _plain(tmp_path, "one")
    entry = dict(_entry("one", one), orchestrator="kubernetes")
    assert mod.plan_conversion([entry]) == {"convert": [], "rootless": [], "rated": [],
                                            "refuse": None, "held": []}


# --- IPv6 --------------------------------------------------------------------- #

@pytest.mark.parametrize("has_ipv6", [True, False])
def test_the_edge_network_has_ipv6_when_the_host_does(tmp_path, monkeypatch, has_ipv6):
    monkeypatch.setattr(mod, "host_has_ipv6", lambda: has_ipv6)
    path = _instance(tmp_path, "one")
    edge = tmp_path / "edge"
    _sync(FakeRunner(), edge, "one", path)
    network = yaml.safe_load((edge / "docker-compose.yml").read_text())["networks"]["edge"]
    assert network.get("enable_ipv6", False) is has_ipv6
    if has_ipv6:
        assert network["ipam"]["config"][0]["subnet"].startswith("fd")
    assert ("bind :::443" in (edge / "conf" / "haproxy.cfg").read_text()) is has_ipv6


def test_certificates_are_made_in_a_directory_under_the_edge(tmp_path):
    # Under canasta-docker the CLI's own /tmp does not exist on the host,
    # so every directory a helper container mounts must live in the edge's.
    path = _instance(tmp_path, "one")
    edge = tmp_path / "edge"
    run = FakeRunner()
    _sync(run, edge, "one", path)
    mounts = [c[i + 1].split(":")[0] for c in run.calls
              for i, a in enumerate(c) if a == "-v"]
    assert mounts and all(m.startswith(str(edge)) for m in mounts), mounts
    assert not [n for n in os.listdir(edge) if n.startswith(".work-")]


def test_a_bad_setting_keeps_the_last_good_configuration(tmp_path):
    one = _instance(tmp_path, "one")
    two = _instance(tmp_path, "two", hosts=("two.example.org",))
    edge = tmp_path / "edge"
    _sync(FakeRunner(), edge, "one", one)
    _sync(FakeRunner(), edge, "two", two)
    good = (edge / "conf" / "haproxy.cfg").read_text()
    with open(os.path.join(two, mod.SNIPPET_FILE), "w") as f:
        f.write("frontend sneaky\n")
    result = _sync(FakeRunner(running=True), edge, "one", one)
    assert "opens a new section" in result["error"]
    assert result["members"] == ["one", "two"] and result["running"]
    assert not result["reload"]
    assert (edge / "conf" / "haproxy.cfg").read_text() == good


def _issued_by_dns(tmp_path, domain_conf, account_conf=""):
    edge = tmp_path / "edge"
    conf = edge / "acme" / "example.com_ecc"
    conf.mkdir(parents=True)
    (conf / "example.com.conf").write_text(
        "Le_Webroot='dns_cf'\nLe_DeployHook='haproxy,'\n" + domain_conf)
    (edge / "acme" / "account.conf").write_text(account_conf)
    return edge


def test_a_rotated_token_reaches_renewals_without_a_reissue(tmp_path):
    edge = _issued_by_dns(tmp_path, "CF_Token='old'\nCF_Zone_ID='z1'\n")
    run = FakeRunner(running=True, listed={"example.com": ("LetsEncrypt.org", True)})
    _issue(tmp_path, run, one={"env": dict(DNS_ENV, CLOUDFLARE_ZONE_ID="z2")})
    assert not run.acme_calls("--issue")
    conf = (edge / "acme" / "example.com_ecc" / "example.com.conf").read_text()
    assert "CF_Token='tok-secret'" in conf and "CF_Zone_ID='z2'" in conf
    assert "'old'" not in conf and "Le_Webroot='dns_cf'" in conf


def test_without_a_zone_id_the_account_keeps_the_token(tmp_path):
    edge = _issued_by_dns(tmp_path, "CF_Token='old'\nCF_Zone_ID='z1'\n",
                          "SAVED_CF_Token='older'\nACCOUNT_EMAIL=''\n")
    run = FakeRunner(running=True, listed={"example.com": ("LetsEncrypt.org", True)})
    _issue(tmp_path, run, one={"env": DNS_ENV})
    assert not run.acme_calls("--issue")
    conf = (edge / "acme" / "example.com_ecc" / "example.com.conf").read_text()
    assert "CF_Token" not in conf and "CF_Zone_ID" not in conf
    account = (edge / "acme" / "account.conf").read_text()
    assert "SAVED_CF_Token='tok-secret'" in account and "ACCOUNT_EMAIL=''" in account


# --- CrowdSec at the edge ----------------------------------------------------- #

CROWDSEC_ENV = {"CANASTA_ENABLE_CROWDSEC": "true", "CROWDSEC_SPOA_API_KEY": "spoa-key"}


def test_an_enrolled_crowdsec_instance_gets_its_bouncer(tmp_path):
    path = _instance(tmp_path, "one", env=CROWDSEC_ENV)
    edge = tmp_path / "edge"
    _sync(FakeRunner(), edge, "one", path)
    layer = yaml.safe_load(open(os.path.join(path, mod.LAYER_FILE)).read())
    spoa = layer["services"]["crowdsec-spoa"]
    assert spoa["image"] == mod.SPOA_IMAGE
    assert "CROWDSEC_KEY=${CROWDSEC_SPOA_API_KEY:-}" in spoa["environment"]
    assert "spoa-key" not in open(os.path.join(path, mod.LAYER_FILE)).read()
    assert spoa["networks"][mod.NETWORK]["aliases"] == ["crowdsec-spoa-one"]
    assert "default" in spoa["networks"]
    cfg = (edge / "conf" / "haproxy.cfg").read_text()
    assert "filter spoe engine crowdsec-one" in cfg
    assert "server spoa crowdsec-spoa-one:9000" in cfg
    assert (edge / "conf" / "spoe-one.cfg").exists()


@pytest.mark.parametrize("env", [
    {"CANASTA_ENABLE_CROWDSEC": "true"},
    {"CROWDSEC_SPOA_API_KEY": "spoa-key"},
])
def test_without_crowdsec_or_its_key_there_is_no_bouncer(tmp_path, env):
    path = _instance(tmp_path, "one", env=env)
    edge = tmp_path / "edge"
    _sync(FakeRunner(), edge, "one", path)
    assert "crowdsec-spoa" not in open(os.path.join(path, mod.LAYER_FILE)).read()
    assert "spoe" not in (edge / "conf" / "haproxy.cfg").read_text()


def test_turning_crowdsec_off_removes_the_bouncer(tmp_path):
    path = _instance(tmp_path, "one", env=CROWDSEC_ENV)
    edge = tmp_path / "edge"
    _sync(FakeRunner(), edge, "one", path)
    with open(os.path.join(path, ".env"), "a") as f:
        f.write("CANASTA_ENABLE_CROWDSEC=false\n")
    run = FakeRunner()
    _sync(run, edge, "one", path)
    assert "crowdsec-spoa" not in open(os.path.join(path, mod.LAYER_FILE)).read()
    assert [c for c in run.calls if c[1:3] == ["ps", "-aq"]
            and "label=com.docker.compose.service=crowdsec-spoa" in c]
    assert not (edge / "conf" / "spoe-one.cfg").exists()


# The image declares these as volumes; without a mount over them, Docker
# makes two anonymous volumes per container that recreate and delete leave.
SPOA_IMAGE_VOLUMES = ["/usr/lib/crowdsec-haproxy-spoa-bouncer/lua",
                      "/var/lib/crowdsec-haproxy-spoa-bouncer/html"]


def test_the_bouncer_mounts_tmpfs_over_its_image_volumes(tmp_path):
    path = _instance(tmp_path, "one", env=CROWDSEC_ENV)
    assert mod.write_layer(path, "one", True, spoa=True)
    layer = yaml.safe_load(open(os.path.join(path, mod.LAYER_FILE)))
    assert sorted(layer["services"]["crowdsec-spoa"]["tmpfs"]) == SPOA_IMAGE_VOLUMES


def test_a_removed_bouncer_takes_its_volumes_with_it():
    calls = []

    def run(argv):
        calls.append(argv)
        return (0, "abc123\n", "") if argv[1] == "ps" else (0, "", "")
    assert mod.remove_stale_spoa(run, "docker", "/srv/one")
    assert calls[-1] == ["docker", "rm", "-f", "-v", "abc123"]


LUA_VOL, HTML_VOL, OTHER_VOL = "a" * 64, "b" * 64, "c" * 64


def _prune_runner(listing, helper_rc=0):
    calls = []

    def run(argv):
        calls.append(argv)
        if argv[1:3] == ["volume", "ls"]:
            return 0, listing, ""
        if argv[1] == "run":
            return helper_rc, ("0 crowdsec.lua template.lua utils.lua\n"
                               "1 ban-with-contact.html ban.html captcha.html lua\n"
                               "2 crowdsec.lua notes.txt\n"), ""
        return 0, "", ""
    return run, calls


def test_leftover_bouncer_volumes_are_removed_and_nothing_else():
    run, calls = _prune_runner("%s\n%s\n%s\nmysite_mysql-data\n" % (LUA_VOL, HTML_VOL, OTHER_VOL))
    assert mod.prune_spoa_volumes(run, "docker") == [LUA_VOL, HTML_VOL]
    helper = next(c for c in calls if c[1] == "run")
    assert "--network" in helper and "none" in helper
    assert "%s:/v/0:ro" % LUA_VOL in helper and "mysite_mysql-data:/v/3:ro" not in helper
    assert calls[-1] == ["docker", "volume", "rm", LUA_VOL, HTML_VOL]


def test_no_candidate_volumes_runs_no_helper():
    run, calls = _prune_runner("mysite_mysql-data\n")
    assert mod.prune_spoa_volumes(run, "docker") == []
    assert [c for c in calls if c[1] == "run"] == []


def test_a_failed_listing_removes_nothing():
    run, calls = _prune_runner("%s\n" % LUA_VOL, helper_rc=1)
    assert mod.prune_spoa_volumes(run, "docker") == []
    assert not [c for c in calls if c[1:3] == ["volume", "rm"]]


def test_the_upgrade_replaces_a_bouncer_that_predates_its_tmpfs_mounts():
    repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(repo, "playbooks", "_upgrade_single.yml")) as f:
        tasks = yaml.safe_load(f)
    names = [t.get("name") for t in tasks]
    order = ["Run upgrade role (migrations + refresh + restart)",
             "Check whether the edge bouncer predates its tmpfs mounts",
             "Rewrite the edge layer with the bouncer's tmpfs mounts",
             "Find the edge bouncer container",
             "Remove the old edge bouncer with its volumes",
             "Start the edge bouncer on the new layer",
             "Remove volumes earlier CrowdSec bouncers left behind"]
    assert [names.index(n) for n in order] == sorted(names.index(n) for n in order)
    remove = tasks[names.index("Remove the old edge bouncer with its volumes")]
    assert "'rm', '-f', '-v'" in remove["ansible.builtin.command"]["argv"]
    start = tasks[names.index("Start the edge bouncer on the new layer")]
    assert "running" in str(start["when"])


def test_the_upgrade_prunes_on_compose_only():
    repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(repo, "playbooks", "_upgrade_single.yml")) as f:
        tasks = {t.get("name"): t for t in yaml.safe_load(f)}
    prune = tasks["Remove volumes earlier CrowdSec bouncers left behind"]
    assert prune["canasta_edge"]["state"] == "prune"
    assert prune["when"] == "not (instance_is_k8s | bool)"


def test_a_new_edge_directory_asks_for_recreated_containers(tmp_path):
    path = _instance(tmp_path, "one")
    edge = tmp_path / "edge"
    assert _sync(FakeRunner(), edge, "one", path)["recreate"]
    assert not _sync(FakeRunner(running=True), edge, "one", path)["recreate"]


@pytest.mark.parametrize("options,expected", [
    ('["name=seccomp,profile=builtin","name=rootless","name=cgroupns"]', True),
    ('["name=apparmor,profile=default","name=seccomp,profile=builtin"]', False),
])
def test_a_rootless_daemon_is_recognized(options, expected):
    calls = []

    def run(argv, env=None):
        calls.append((argv, env))
        return 0, options + "\n", ""
    assert mod.daemon_rootless(run, "docker", "unix:///run/user/1000/docker.sock") is expected
    assert calls[0][1] == {"DOCKER_HOST": "unix:///run/user/1000/docker.sock"}


@pytest.mark.parametrize("host,network", [(True, False), (False, True)])
def test_an_existing_network_keeps_its_ipv6_setting(tmp_path, monkeypatch, host, network):
    monkeypatch.setattr(mod, "host_has_ipv6", lambda: host)
    path = _instance(tmp_path, "one")
    edge = tmp_path / "edge"
    result = _sync(FakeRunner(network_ipv6=network), edge, "one", path)
    assert result["network_ipv6_pending"] is host
    compose = yaml.safe_load((edge / "docker-compose.yml").read_text())
    assert compose["networks"]["edge"].get("enable_ipv6", False) is network
    assert ("bind :::443" in (edge / "conf" / "haproxy.cfg").read_text()) is network


def test_a_matching_network_reports_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "host_has_ipv6", lambda: True)
    path = _instance(tmp_path, "one")
    result = _sync(FakeRunner(network_ipv6=True), tmp_path / "edge", "one", path)
    assert "network_ipv6_pending" not in result


# --- rootless Podman: HAProxy from systemd units ------------------------------ #

@pytest.fixture
def user_units(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    return tmp_path / "xdg" / "systemd" / "user"


def test_under_podman_haproxy_runs_from_systemd_units(tmp_path, user_units):
    path = _instance(tmp_path, "one")
    edge = tmp_path / "edge"
    result = mod.sync(FakeRunner(), "podman", str(edge), "one", path, None)
    assert result["sockets"] and result["units_changed"]
    compose = yaml.safe_load((edge / "docker-compose.yml").read_text())
    assert "haproxy" not in compose["services"]
    assert compose["services"]["acme"]["networks"] == {"edge": {}}
    assert compose["volumes"]["run"]["name"] == mod.RUN_VOLUME
    socket = (user_units / mod.SOCKET_UNIT).read_text()
    assert socket.index("ListenStream=0.0.0.0:80") < socket.index("ListenStream=0.0.0.0:443")
    assert "[::]" not in socket
    service = (user_units / mod.SERVICE_UNIT).read_text()
    assert "--network %s" % mod.NETWORK in service and mod.HAPROXY_IMAGE in service
    assert "-v %s:%s:ro" % (edge / "conf", mod.CONFIG_DIR) in service
    assert "-v %s:/var/run/haproxy" % mod.RUN_VOLUME in service
    cfg = (edge / "conf" / "haproxy.cfg").read_text()
    assert "    bind fd@3\n" in cfg and "    bind fd@4 ssl crt-list" in cfg
    assert "bind :80" not in cfg


def test_under_podman_with_ipv6_the_socket_unit_listens_on_both(tmp_path, user_units, monkeypatch):
    monkeypatch.setattr(mod, "host_has_ipv6", lambda: True)
    path = _instance(tmp_path, "one")
    edge = tmp_path / "edge"
    mod.sync(FakeRunner(), "podman", str(edge), "one", path, None)
    socket = (user_units / mod.SOCKET_UNIT).read_text()
    lines = [line for line in socket.splitlines() if line.startswith("ListenStream=")]
    assert lines == ["ListenStream=0.0.0.0:80", "ListenStream=0.0.0.0:443",
                     "ListenStream=[::]:80", "ListenStream=[::]:443"]
    assert "BindIPv6Only=ipv6-only" in socket
    cfg = (edge / "conf" / "haproxy.cfg").read_text()
    assert "bind fd@5\n" in cfg and "bind fd@6 ssl crt-list" in cfg


def test_docker_keeps_haproxy_in_compose(tmp_path, user_units):
    path = _instance(tmp_path, "one")
    edge = tmp_path / "edge"
    result = _sync(FakeRunner(), edge, "one", path)
    assert not result["sockets"] and "units_changed" not in result
    assert "haproxy" in yaml.safe_load((edge / "docker-compose.yml").read_text())["services"]
    assert not user_units.exists()


def test_starting_under_podman_enables_the_socket_and_starts_haproxy(tmp_path, user_units):
    run = FakeRunner(running=True)
    mod.start_edge(run, "podman", "podman-compose", str(tmp_path), restart=True)
    assert run.calls[0][:5] == ["podman-compose", "-p", "canasta-edge", "up", "-d"]
    systemctl = [c[2:] for c in run.calls if c[:2] == ["systemctl", "--user"]]
    assert ["enable", "--now", mod.SOCKET_UNIT] in systemctl
    assert ["restart", mod.SERVICE_UNIT] in systemctl
    assert all(e.get("XDG_RUNTIME_DIR") for c, e in zip(run.calls, run.envs)
               if c[:2] == ["systemctl", "--user"])


def test_starting_under_docker_uses_compose_only(tmp_path):
    run = FakeRunner(running=True)
    mod.start_edge(run, "docker", "docker compose", str(tmp_path), recreate=True)
    assert run.calls == [["docker", "compose", "-p", "canasta-edge", "up", "-d", "--force-recreate"]]


def test_stopping_under_podman_removes_the_units(tmp_path, user_units):
    user_units.mkdir(parents=True)
    for unit in (mod.SOCKET_UNIT, mod.SERVICE_UNIT):
        (user_units / unit).write_text("x")
    run = FakeRunner()
    mod.stop_edge(run, "podman", "podman-compose", str(tmp_path))
    assert ["systemctl", "--user", "disable", "--now", mod.SERVICE_UNIT, mod.SOCKET_UNIT] in run.calls
    assert not list(user_units.iterdir())
    assert run.calls[-1] == ["podman-compose", "-p", "canasta-edge", "down", "-v"]


def test_stopping_removes_the_hostnames_and_keys_but_keeps_the_ca_and_account(tmp_path):
    for sub in ("conf/certs", "ca", "acme"):
        (tmp_path / sub).mkdir(parents=True)
    (tmp_path / "conf" / "hosts.map").write_text("wiki.example.com@443 one/default\n")
    (tmp_path / "conf" / "certs" / "wiki.example.com.pem").write_text("key")
    (tmp_path / "ca" / "ca.pem").write_text("ca")
    (tmp_path / "acme" / "thumbprint").write_text("t")
    mod.stop_edge(FakeRunner(), "docker", "docker compose", str(tmp_path))
    assert not (tmp_path / "conf").exists()
    assert (tmp_path / "ca" / "ca.pem").exists()
    assert (tmp_path / "acme" / "thumbprint").exists()


def test_a_failed_stop_leaves_the_files(tmp_path):
    (tmp_path / "conf").mkdir()

    class Failing(FakeRunner):
        def __call__(self, argv, env=None, cwd=None):
            return (1, "", "boom")
    with pytest.raises(mod.EdgeError):
        mod.stop_edge(Failing(), "docker", "docker compose", str(tmp_path))
    assert (tmp_path / "conf").exists()


def test_the_check_runs_without_the_systemd_sockets(tmp_path, user_units):
    seen = {}

    class Capture(FakeRunner):
        def __call__(self, argv, env=None, cwd=None):
            if argv[1] == "run" and "haproxy" in argv and "-c" in argv:
                stage = next(a.split(":")[0] for a in argv if a.endswith(":%s:ro" % mod.CONFIG_DIR))
                seen["cfg"] = open(os.path.join(stage, "haproxy.cfg")).read()
            return super().__call__(argv, env, cwd)
    path = _instance(tmp_path, "one")
    edge = tmp_path / "edge"
    mod.sync(Capture(), "podman", str(edge), "one", path, None)
    assert "fd@" not in seen["cfg"] and "bind 127.0.0.1:18003" in seen["cfg"]
    assert "bind fd@3" in (edge / "conf" / "haproxy.cfg").read_text()


def test_without_instances_no_units_are_written_and_leftovers_are_reported(tmp_path, user_units):
    path = _instance(tmp_path, "one")
    edge = tmp_path / "edge"
    (edge / "conf").mkdir(parents=True)
    user_units.mkdir(parents=True)
    (user_units / mod.SOCKET_UNIT).write_text("left over")
    result = mod.sync(FakeRunner(), "podman", str(edge), "one", path, "absent")
    assert result["members"] == [] and "units_changed" not in result
    assert result["units_present"]
    assert (user_units / mod.SOCKET_UNIT).read_text() == "left over"


def test_the_edge_listens_on_every_instance_port(tmp_path, user_units):
    own = {"HTTP_PORT": "8080", "HTTPS_PORT": "8443"}
    path = _instance(tmp_path, "one", hosts=("example.com:8443",), env=own)
    edge = tmp_path / "edge"
    result = _sync(FakeRunner(), edge, "one", path)
    assert result["ports"] == [8080, 8443]
    compose = yaml.safe_load((edge / "docker-compose.yml").read_text())
    assert compose["services"]["haproxy"]["ports"] == ["8080:8080", "8443:8443"]
    podman_edge = tmp_path / "podman-edge"
    mod.sync(FakeRunner(), "podman", str(podman_edge), "one", path, None)
    socket = (user_units / mod.SOCKET_UNIT).read_text()
    assert [line for line in socket.splitlines() if line.startswith("ListenStream=")] == [
        "ListenStream=0.0.0.0:8080", "ListenStream=0.0.0.0:8443"]


def test_haproxy_comes_back_whenever_the_daemon_restarts(tmp_path, user_units):
    # unless-stopped skips a container Docker counts as manually stopped,
    # which any `docker kill` marks it as.
    path = _instance(tmp_path, "one")
    edge = tmp_path / "edge"
    _sync(FakeRunner(), edge, "one", path)
    services = yaml.safe_load((edge / "docker-compose.yml").read_text())["services"]
    assert services["haproxy"]["restart"] == "always"


CAPPED_LOG = {"driver": "json-file", "options": {"max-size": "50m", "max-file": "3"}}


def test_the_edge_logs_are_capped_under_docker(tmp_path, user_units):
    # HAProxy logs every request; an uncapped json-file log fills the disk.
    path = _instance(tmp_path, "one")
    edge = tmp_path / "edge"
    _sync(FakeRunner(), edge, "one", path)
    services = yaml.safe_load((edge / "docker-compose.yml").read_text())["services"]
    assert services["haproxy"]["logging"] == CAPPED_LOG
    assert services["acme"]["logging"] == CAPPED_LOG


def test_the_edge_logs_are_capped_under_podman(tmp_path, user_units):
    path = _instance(tmp_path, "one")
    edge = tmp_path / "edge"
    mod.sync(FakeRunner(), "podman", str(edge), "one", path, None)
    services = yaml.safe_load((edge / "docker-compose.yml").read_text())["services"]
    assert services["acme"]["logging"] == CAPPED_LOG
    service = (user_units / mod.SERVICE_UNIT).read_text()
    assert "--log-driver k8s-file --log-opt max-size=50m" in service


# --- TLS_MODE_HOSTS ------------------------------------------------------------ #

def test_only_hostnames_set_to_acme_are_issued(tmp_path):
    run = FakeRunner(running=True)
    result = _issue(tmp_path, run, one={
        "hosts": ("example.com", "www.example.com"),
        "env": {"TLS_MODE": "internal", "TLS_MODE_HOSTS": "example.com=acme"}})
    assert result["issued"] == ["example.com"]
    assert [c[c.index("-d") + 1] for c in run.acme_calls("--issue")] == ["example.com"]


def test_an_internal_edge_with_no_acme_host_never_contacts_lets_encrypt(tmp_path):
    path = _instance(tmp_path, "one", hosts=("example.com", "www.example.com"),
                     env={"TLS_MODE": "acme",
                          "TLS_MODE_HOSTS": "example.com=internal,www.example.com=internal"})
    run = FakeRunner()
    result = _sync(run, tmp_path / "edge", "one", path)
    assert not result["acme"]
    assert not [c for c in run.calls if "--register-account" in c[-1]]


def test_an_acme_dns_host_uses_the_dns_hook_and_the_rest_http(tmp_path):
    run = FakeRunner(running=True)
    _issue(tmp_path, run, one={
        "hosts": ("example.com", "www.example.com"),
        "env": {"TLS_MODE": "acme", "TLS_MODE_HOSTS": "www.example.com=acme-dns",
                "CLOUDFLARE_API_TOKEN": "tok-secret"}})
    methods = {c[c.index("-d") + 1]: c[c.index("--issue") + 1]
               for c in run.acme_calls("--issue")}
    assert methods == {"example.com": "--stateless", "www.example.com": "--dns"}


# --- localized Special: names ------------------------------------------------- #

def _siteinfo(name, aliases=()):
    return json.dumps({"query": {
        "namespaces": {"-1": {"id": -1, "canonical": "Special", "*": name},
                       "0": {"id": 0, "*": ""}},
        "namespacealiases": [{"id": -1, "*": a} for a in aliases]}}, ensure_ascii=False)


class WikiRunner(FakeRunner):
    """Answers siteinfo requests made in the web container from `replies`,
    {wikis.yaml url: reply body, or None for no answer}."""

    def __init__(self, replies, web=True, **kwargs):
        super().__init__(**kwargs)
        self.replies = dict(replies)
        self.web = web

    @staticmethod
    def _url(argv):
        path = argv[-1][len("http://127.0.0.1"):-len(mod.NAMES_QUERY)]
        return argv[argv.index("--header") + 1][len("Host: "):] + path

    def asked(self):
        return [self._url(c) for c in self.calls if c[1] == "exec" and "wget" in c]

    def reloads(self):
        return [c for c in self.calls if c[1:3] == ["exec", mod.CONTAINER] and "-USR2" in c]

    def __call__(self, argv, env=None, cwd=None):
        if argv[1] == "ps" and "label=com.docker.compose.service=web" in argv:
            self.calls.append(argv)
            return 0, "webid\n" if self.web else "", ""
        if argv[1] == "exec" and "wget" in argv:
            self.calls.append(argv)
            reply = self.replies.get(self._url(argv))
            return (0, reply, "") if reply is not None else (4, "", "")
        return super().__call__(argv, env, cwd)


@pytest.fixture(autouse=True)
def _no_retry_delay(monkeypatch):
    monkeypatch.setattr(mod, "NAMES_RETRY_DELAY", 0)


def _learn(run, edge, inst_id, path):
    return mod.learn(run, "docker", str(edge), inst_id, path)


def _german(tmp_path, hosts=("de.example.org",)):
    path = _instance(tmp_path, "de", hosts=hosts)
    edge = tmp_path / "edge"
    _sync(FakeRunner(), edge, "de", path)
    return path, edge


def test_learned_names_become_the_instances_dynamic_list(tmp_path):
    path, edge = _german(tmp_path, ("de.example.org", "de.example.org/fr"))
    run = WikiRunner({"de.example.org": _siteinfo("Spezial", ["Spécial"]),
                      "de.example.org/fr": None}, running=True)
    result = _learn(run, edge, "de", path)
    assert result["changed"] and result["reload"] and "warning" not in result
    assert (edge / "conf" / "dynamic-de.lst").read_text() == \
        "Spezial:\nSpezial%3A\nSp%C3%A9cial:\nSp%C3%A9cial%3A\n"
    assert "url_sub -i -f /usr/local/etc/haproxy/dynamic-de.lst" in \
        (edge / "conf" / "haproxy.cfg").read_text()
    assert len(run.reloads()) == 1
    assert json.loads((edge / "special-names" / "de.json").read_text()) == \
        {"w0": ["Spezial", "Spécial"]}


def test_the_request_goes_to_the_web_container_by_hostname_and_path(tmp_path):
    path, edge = _german(tmp_path, ("de.example.org", "de.example.org/fr"))
    run = WikiRunner({"de.example.org": _siteinfo("Spezial")})
    _learn(run, edge, "de", path)
    urls = [c[-1] for c in run.calls if c[1] == "exec" and "wget" in c]
    assert urls[0] == "http://127.0.0.1" + mod.NAMES_QUERY
    assert urls[1] == "http://127.0.0.1/fr" + mod.NAMES_QUERY
    assert all(c[2] == "webid" for c in run.calls if c[1] == "exec" and "wget" in c)


def test_unchanged_names_neither_render_nor_reload(tmp_path):
    path, edge = _german(tmp_path)
    _learn(WikiRunner({"de.example.org": _siteinfo("Spezial")}, running=True), edge, "de", path)
    run = WikiRunner({"de.example.org": _siteinfo("Spezial")}, running=True)
    result = _learn(run, edge, "de", path)
    assert not result["changed"] and not result["reload"]
    assert run.runs("haproxy") == [] and run.reloads() == []
    assert [c for c in run.calls if c[1] != "exec" and "service=web" not in " ".join(c)] == []


def test_an_english_wiki_leaves_the_edge_as_it_was(tmp_path):
    path, edge = _german(tmp_path)
    before = (edge / "conf" / "haproxy.cfg").read_text()
    run = WikiRunner({"de.example.org": _siteinfo("Special")}, running=True)
    result = _learn(run, edge, "de", path)
    assert not result["reload"] and run.reloads() == [] and run.runs("haproxy") == []
    assert (edge / "conf" / "haproxy.cfg").read_text() == before
    assert not (edge / "conf" / "dynamic-de.lst").exists()


def test_a_silent_wiki_keeps_its_last_names(tmp_path):
    path, edge = _german(tmp_path, ("de.example.org", "fr.example.org"))
    _learn(WikiRunner({"de.example.org": _siteinfo("Spezial"),
                       "fr.example.org": _siteinfo("Spécial")}), edge, "de", path)
    run = WikiRunner({"de.example.org": _siteinfo("Spezial"), "fr.example.org": None})
    result = _learn(run, edge, "de", path)
    assert not result["changed"]
    assert run.asked().count("fr.example.org") == mod.NAMES_ATTEMPTS
    assert "Sp%C3%A9cial:" in (edge / "conf" / "dynamic-de.lst").read_text()


def test_a_language_change_replaces_the_names(tmp_path):
    path, edge = _german(tmp_path)
    _learn(WikiRunner({"de.example.org": _siteinfo("Spezial")}), edge, "de", path)
    run = WikiRunner({"de.example.org": _siteinfo("Special")}, running=True)
    assert _learn(run, edge, "de", path)["reload"]
    assert not (edge / "conf" / "dynamic-de.lst").exists()
    assert "dynamic-de.lst" not in (edge / "conf" / "haproxy.cfg").read_text()


def test_a_wiki_that_answers_late_is_asked_again(tmp_path):
    path, edge = _german(tmp_path)

    class Late(WikiRunner):
        def __call__(self, argv, env=None, cwd=None):
            if argv[1] == "exec" and "wget" in argv and not self.asked():
                self.calls.append(argv)
                return 8, "", ""
            return super().__call__(argv, env, cwd)
    run = Late({"de.example.org": _siteinfo("Spezial")})
    _learn(run, edge, "de", path)
    assert run.asked() == ["de.example.org", "de.example.org"]
    assert (edge / "conf" / "dynamic-de.lst").exists()


def test_names_survive_the_edge_stopping(tmp_path):
    path, edge = _german(tmp_path)
    _learn(WikiRunner({"de.example.org": _siteinfo("Spezial")}), edge, "de", path)
    mod.stop_edge(FakeRunner(), "docker", "docker compose", str(edge))
    assert not (edge / "conf").exists()
    _sync(FakeRunner(), edge, "de", path)
    assert (edge / "conf" / "dynamic-de.lst").read_text() == "Spezial:\nSpezial%3A\n"


def test_leaving_the_edge_forgets_the_names(tmp_path):
    path, edge = _german(tmp_path)
    _learn(WikiRunner({"de.example.org": _siteinfo("Spezial")}), edge, "de", path)
    _sync(FakeRunner(), edge, "de", path, member="absent")
    assert not (edge / "special-names" / "de.json").exists()


def test_an_instance_not_behind_the_edge_is_not_asked(tmp_path):
    path = _instance(tmp_path, "k8s")
    run = WikiRunner({"example.com": _siteinfo("Spezial")})
    assert _learn(run, tmp_path / "edge", "k8s", path) == {"changed": False, "reload": False}
    assert run.calls == [] and not (tmp_path / "edge").exists()


def test_without_a_running_web_container_nothing_changes(tmp_path):
    path, edge = _german(tmp_path)
    run = WikiRunner({"de.example.org": _siteinfo("Spezial")}, web=False)
    assert not _learn(run, edge, "de", path)["changed"]
    assert run.asked() == [] and not (edge / "special-names").exists()


@pytest.mark.parametrize("reply", ["not json", "", '{"error": {"code": "readapidenied"}}',
                                   "<html>500</html>"])
def test_an_answer_without_names_counts_as_silence(tmp_path, reply):
    path, edge = _german(tmp_path)
    _learn(WikiRunner({"de.example.org": _siteinfo("Spezial")}), edge, "de", path)
    assert not _learn(WikiRunner({"de.example.org": reply}), edge, "de", path)["changed"]
    assert (edge / "conf" / "dynamic-de.lst").exists()


@pytest.mark.parametrize("url", ["de.example.org\nHost: evil", "de.example.org/a b",
                                 "de.example.org/$(id)", "-O/tmp/x", "http://de.example.org"])
def test_an_unusable_wiki_url_is_not_requested(tmp_path, url):
    path, edge = _german(tmp_path)
    run = WikiRunner({})
    assert mod.wiki_special_names(run, "docker", "webid", url) is None
    assert run.calls == []


def test_hostile_names_never_reach_the_edge(tmp_path):
    path, edge = _german(tmp_path)
    reply = _siteinfo("Spezial\nbackend x", ["} or 1", "Spe%zial", "Spezial"])
    _learn(WikiRunner({"de.example.org": reply}), edge, "de", path)
    assert (edge / "conf" / "dynamic-de.lst").read_text() == "Spezial:\nSpezial%3A\n"


def test_a_render_error_keeps_the_previous_names(tmp_path):
    path, edge = _german(tmp_path)
    _learn(WikiRunner({"de.example.org": _siteinfo("Spezial")}), edge, "de", path)
    before = (edge / "special-names" / "de.json").read_text()
    run = WikiRunner({"de.example.org": _siteinfo("Spécial")}, running=True, invalid=True)
    result = _learn(run, edge, "de", path)
    assert "bad config" in result["warning"] and not result["reload"]
    assert (edge / "special-names" / "de.json").read_text() == before
    assert (edge / "conf" / "dynamic-de.lst").read_text() == "Spezial:\nSpezial%3A\n"


# --- wikis answering through the edge ----------------------------------------- #

def _wiki_server(answers, tls_dir=None, seen=None):
    """A server on the loopback that answers each request with the next
    (status, content type, body) of answers, repeating the last one."""
    import http.server
    import ssl
    answers = list(answers)

    class Wiki(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if seen is not None:
                seen.append((self.headers.get("Host"), self.path))
            status, ctype, body = answers.pop(0) if len(answers) > 1 else answers[0]
            body = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = _serve(Wiki)
    if tls_dir:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(os.path.join(tls_dir, "c.pem"), os.path.join(tls_dir, "k.pem"))
        if seen is not None:
            context.sni_callback = lambda sock, name, ctx: seen.append(("sni", name))
        server.socket = context.wrap_socket(server.socket, server_side=True)
    return server


def _stop(server):
    server.shutdown()
    server.server_close()


SITEINFO_OK = (200, "application/json", '{"batchcomplete": ""}')


def test_each_wiki_is_requested_under_its_host_and_path():
    assert mod.wiki_requests([
        {"id": "main", "url": "example.com"},
        {"id": "docs", "url": "example.com/docs/"},
        {"id": "alt", "url": "localhost:8443"},
        {"id": "none", "url": None},
    ]) == [
        ("main", "example.com", "example.com", mod.SITEINFO),
        ("docs", "example.com/docs", "example.com", "/docs" + mod.SITEINFO),
        ("alt", "localhost:8443", "localhost:8443", mod.SITEINFO),
    ]


def test_an_answer_from_behind_the_edge_counts_whatever_its_status():
    for answer in (SITEINFO_OK, (301, "text/html", ""), (401, "text/html", "auth"),
                   (404, "text/html", "Not Found")):
        server = _wiki_server([answer])
        try:
            assert mod.wiki_answer(server.server_port, False, "example.com",
                                   mod.SITEINFO) is None
        finally:
            _stop(server)


def test_the_edges_own_refusals_and_server_errors_are_reasons():
    cases = (
        ((503, "text/plain", "This site is not running.\n"),
         "HTTP 503: This site is not running"),
        ((503, "text/html", "<html>\n<h1>Backend fetch failed</h1>\n</html>"), "HTTP 503"),
        ((404, "text/plain", mod.NO_SITE + "\n"), "the edge serves no site at this address"),
    )
    for answer, reason in cases:
        server = _wiki_server([answer])
        try:
            assert mod.wiki_answer(server.server_port, False, "example.com",
                                   mod.SITEINFO) == reason
        finally:
            _stop(server)


def test_a_port_nothing_listens_on_is_a_reason():
    import socket
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    assert mod.wiki_answer(port, False, "example.com", mod.SITEINFO).startswith(
        "port %d: " % port)


def test_a_wiki_that_does_not_answer_in_time_is_a_reason():
    import socket
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        reason = mod.wiki_answer(sock.getsockname()[1], False, "example.com",
                                 mod.SITEINFO, timeout=1)
    assert reason == mod.NO_ANSWER


@pytest.mark.skipif(not shutil.which("openssl"), reason="needs openssl")
def test_https_names_the_wiki_and_skips_the_certificate_check(tmp_path):
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
         "-subj", "/CN=other.example", "-keyout", str(tmp_path / "k.pem"),
         "-out", str(tmp_path / "c.pem")], check=True, capture_output=True)
    seen = []
    server = _wiki_server([SITEINFO_OK], tls_dir=str(tmp_path), seen=seen)
    try:
        assert mod.wiki_answer(server.server_port, True, "example.com",
                               "/docs" + mod.SITEINFO) is None
    finally:
        _stop(server)
    assert seen == [("sni", "example.com"), ("example.com", "/docs" + mod.SITEINFO)]


def test_wikis_are_asked_again_until_they_answer(tmp_path, monkeypatch):
    monkeypatch.setattr(mod.time, "sleep", lambda seconds: None)
    seen = []
    server = _wiki_server([(503, "text/plain", "This site is not running.\n")] * 3
                          + [SITEINFO_OK], seen=seen)
    try:
        path = _instance(tmp_path, "one", hosts=("example.com", "example.com/docs"),
                         env={"TLS_MODE": "off", "HTTP_PORT": str(server.server_port)})
        assert mod.unanswered_wikis("one", path) == []
    finally:
        _stop(server)
    # Each wiki until it answers, and not again after.
    assert [p for _, p in seen] == [mod.SITEINFO, "/docs" + mod.SITEINFO] * 2 + [mod.SITEINFO]


def test_the_wikis_still_not_answering_are_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "ANSWER_WAIT", 0)
    server = _wiki_server([(502, "text/plain", "")])
    try:
        path = _instance(tmp_path, "one", hosts=("example.com",),
                         env={"TLS_MODE": "off", "HTTP_PORT": str(server.server_port)})
        assert mod.unanswered_wikis("one", path) == [
            {"wiki": "w0", "url": "example.com", "reason": "HTTP 502"}]
    finally:
        _stop(server)


def test_a_wiki_that_never_answers_is_reported_with_the_whole_wait(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "ANSWER_WAIT", 0)
    monkeypatch.setattr(mod, "wiki_answer",
                        lambda port, tls, server, path, timeout: mod.NO_ANSWER)
    path = _instance(tmp_path, "one", hosts=("example.com",), env={"TLS_MODE": "off"})
    assert mod.unanswered_wikis("one", path) == [
        {"wiki": "w0", "url": "example.com", "reason": "no answer within 0s"}]


def test_an_instance_served_over_https_is_asked_on_its_https_port(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "ANSWER_WAIT", 0)
    asked = []
    monkeypatch.setattr(mod, "wiki_answer",
                        lambda port, tls, server, path, timeout: asked.append((port, tls)))
    path = _instance(tmp_path, "one", env={"HTTP_PORT": "8080", "HTTPS_PORT": "8443"})
    assert mod.unanswered_wikis("one", path) == []
    assert asked == [(8443, True)]


def test_the_cli_container_does_not_probe_its_own_loopback(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "unanswered_wikis", lambda inst_id, path: 1 / 0)
    monkeypatch.setenv("CANASTA_RUN_MODE", "docker")
    assert mod.probe("one", str(tmp_path)) == {"changed": False, "probed": False,
                                               "unanswered": []}
    monkeypatch.delenv("CANASTA_RUN_MODE")
    monkeypatch.setattr(mod, "unanswered_wikis", lambda inst_id, path: ["w0"])
    assert mod.probe("one", str(tmp_path))["unanswered"] == ["w0"]
