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

    def __init__(self, containers=None, running=False, invalid=False,
                 listed=None, issue_fails=()):
        self.containers = containers or {}
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

    def __call__(self, argv, env=None):
        self.calls.append(argv)
        self.envs.append(env or {})
        if argv[1] == "exec" and "acme.sh" in argv:
            return self._acme(argv[argv.index("acme.sh") + 1:])
        if argv[1] == "exec":
            return 0, "", ""
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
    def run(argv, env=None):
        proc = subprocess.run(argv, capture_output=True, text=True,
                              env=dict(_DOCKER_ENV, **(env or {})))
        return proc.returncode, proc.stdout, proc.stderr

    # internal: acme would register a real Let's Encrypt account.
    path = _instance(tmp_path, "one", hosts=("example.com", "www.example.com"),
                     media=("images.example.com",),
                     env={"CADDY_TLS_MODE": "internal"})
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
    path = _instance(tmp_path, "one", env={"CADDY_TLS_MODE": "internal"})
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


def test_a_failure_is_reported_without_failing(tmp_path):
    run = FakeRunner(running=True, issue_fails={"example.com"})
    result = _issue(tmp_path, run, one={})
    assert result["issued"] == []
    assert result["unissued"][0]["hostname"] == "example.com"
    assert "Invalid response" in result["unissued"][0]["reason"]
    assert run.acme_calls("--deploy") == []


def test_a_hostname_gone_from_lets_encrypt_is_removed_and_replaced(tmp_path):
    """A hostname switched to internal keeps being served, but from the
    edge's CA: its Let's Encrypt PEM is replaced and HAProxy reloaded."""
    run = FakeRunner(running=True, listed={"example.com": ("LetsEncrypt.org", True),
                                           "old.example.org": ("LetsEncrypt.org", True)})
    result = _issue(tmp_path, run, one={"env": {"CADDY_TLS_MODE": "internal"}})
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

    def __call__(self, argv, env=None):
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
    assert mod.helper_user(OwnerProbe(0), "docker", str(edge)) == "1000:1000"
    assert (edge / ".helper-user").read_text() == "1000:1000\n"
    assert mod.helper_user(FakeRunner(), "docker", str(edge)) == "1000:1000"


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

DNS_ENV = {"CADDY_TLS_MODE": "acme-dns", "CLOUDFLARE_API_TOKEN": "tok-secret"}


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
    path = _instance(tmp_path, "one", env={"CADDY_TLS_MODE": "acme-dns"})
    with pytest.raises(mod.EdgeError, match="CLOUDFLARE_API_TOKEN is not set"):
        _sync(FakeRunner(), tmp_path / "edge", "one", path)


def test_different_tokens_without_zone_ids_are_refused(tmp_path):
    edge = tmp_path / "edge"
    one = _instance(tmp_path, "one", env=DNS_ENV)
    two = _instance(tmp_path, "two", hosts=("two.example.org",),
                    env=dict(DNS_ENV, CLOUDFLARE_API_TOKEN="other"))
    _sync(FakeRunner(), edge, "one", one)
    with pytest.raises(mod.EdgeError, match="Set CLOUDFLARE_ZONE_ID for each"):
        _sync(FakeRunner(), edge, "two", two)


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
    path = _instance(tmp_path, "one", env={"CADDY_TRUSTED_PROXIES": "cloudflare"})
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
    path = _instance(tmp_path, "one", env={"CADDY_TLS_MODE": "internal"})
    with open(os.path.join(path, "config", "Caddyfile.global"), "w") as f:
        f.write("{\n    email a@example.com\n}\n"
                "www.example.com {\n    redir https://example.com{uri}\n}\n"
                "*.example.org {\n    respond x\n}\n")
    edge = tmp_path / "edge"
    result = _sync(FakeRunner(), edge, "one", path)
    assert "www.example.com one/wiki" in (edge / "conf" / "hosts.map").read_text()
    assert "www.example.com.pem" in os.listdir(edge / "conf" / "certs")
    assert result["unrouted_sites"] == [
        {"instance": "one", "address": "*.example.org", "reason": "it is a wildcard"}]
