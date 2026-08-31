"""Guards for declarative media hosts — object-storage-backed image hostnames.

Serving `images.example.com` from a bucket means hand-writing a Caddy snippet
in Caddyfile.global, and the working configuration is not obvious. Each
directive below corresponds to a specific failure:

  * without the Host rewrite, object storage returns NoSuchBucket — it selects
    the bucket from the Host header, not the path;
  * without a CONSTANT Access-Control-Allow-Origin, MultimediaViewer fails
    every image load, because it requests with crossOrigin="anonymous". A
    reflected origin is worse than none once a CDN caches the response, since
    the cached value is then wrong for the next requester;
  * Cookie and Authorization must be stripped, or the wiki's session is
    forwarded to the storage provider;
  * tls_server_name must match the provider, not the public hostname.

These tests pin those four, plus the two properties that make the feature safe
to land: an absent declaration file changes nothing, and a hostname already
served by Caddyfile.global is refused rather than rendered — Caddy will not
load a configuration that claims one hostname twice.
"""

import os

import yaml
from jinja2 import Environment, FileSystemLoader

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
TEMPLATES = os.path.join(REPO_ROOT, "roles", "orchestrator", "templates")
REWRITE_CADDY = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "rewrite_caddy.yml")

BASE = dict(
    _site_address="www.example.com", _backend="varnish:80",
    _redirect_server_names=[], _observable=False, _http_only=False,
    _crowdsec_active=False, _crowdsec_enabled=False, _staging_certs=False,
    _trusted_proxies_enabled=False, _tls_cert="origin.crt",
    _tls_key="origin.key", _tls_mode="acme",
)

HOST = {"hostname": "images.example.com", "bucket": "bucket-a",
        "endpoint": "region.provider.com"}


def _render(**over):
    """Render Caddyfile.j2 the way ansible.builtin.template does."""
    env = Environment(loader=FileSystemLoader(TEMPLATES), trim_blocks=True,
                      lstrip_blocks=False, keep_trailing_newline=True)
    return env.get_template("Caddyfile.j2").render(**{**BASE, **over})


def _media_block(out, hostname="images.example.com"):
    start = out.index(hostname + " {")
    return out[start:out.index("\n}", start)]


def _walk_tasks(tasks):
    for t in tasks or []:
        if not isinstance(t, dict):
            continue
        yield t
        for nested in ("block", "rescue", "always"):
            if nested in t:
                yield from _walk_tasks(t[nested])


def _tasks():
    with open(REWRITE_CADDY) as f:
        return list(_walk_tasks(yaml.safe_load(f)))


class TestAbsentDeclarationChangesNothing:
    def test_no_media_hosts_renders_no_blocks(self):
        """config/media-hosts.yaml is optional; every existing instance lacks
        it and must render byte-identically to before."""
        out = _render(_media_hosts=[])
        assert "media.log" not in out

    def test_the_fact_defaults_to_empty(self):
        """A missing file must not raise — the slurp is skipped, so the parse
        has to tolerate an undefined register."""
        src = open(REWRITE_CADDY).read()
        assert "_media_hosts_raw.content | default('')" in src
        assert "media_hosts | default([], true)" in src


class TestTheFourNonObviousRequirements:
    def test_host_header_is_rewritten_to_the_bucket(self):
        """Without this the provider returns NoSuchBucket."""
        blk = _media_block(_render(_media_hosts=[HOST]))
        assert "header_up Host bucket-a.region.provider.com" in blk

    def test_access_control_allow_origin_is_constant(self):
        """MultimediaViewer requests with crossOrigin="anonymous". A reflected
        origin breaks once a CDN caches the response."""
        blk = _media_block(_render(_media_hosts=[HOST]))
        assert 'header Access-Control-Allow-Origin "*"' in blk
        assert "{http.request.header.Origin}" not in blk

    def test_credentials_are_not_forwarded_upstream(self):
        blk = _media_block(_render(_media_hosts=[HOST]))
        assert "header_up -Cookie" in blk
        assert "header_up -Authorization" in blk

    def test_sni_matches_the_provider_not_the_public_hostname(self):
        blk = _media_block(_render(_media_hosts=[HOST]))
        assert "tls_server_name bucket-a.region.provider.com" in blk
        assert "tls_server_name images.example.com" not in blk


class TestMediaBlocksInheritTheTlsMode:
    """The point of generating these blocks is that the operator no longer
    maintains the TLS directive in two files."""

    def test_internal(self):
        blk = _media_block(_render(_tls_mode="internal", _media_hosts=[HOST]))
        assert "tls internal" in blk

    def test_custom(self):
        blk = _media_block(_render(_tls_mode="custom", _media_hosts=[HOST]))
        assert "tls /etc/caddy/certs/origin.crt /etc/caddy/certs/origin.key" in blk

    def test_acme_emits_nothing(self):
        blk = _media_block(_render(_tls_mode="acme", _media_hosts=[HOST]))
        assert not any(l.strip().startswith("tls ") for l in blk.splitlines())

    def test_skipped_on_kubernetes(self):
        """The ingress terminates TLS there; Caddy serves plain HTTP."""
        blk = _media_block(
            _render(_tls_mode="internal", _http_only=True, _media_hosts=[HOST]))
        assert "tls internal" not in blk


class TestMultipleHosts:
    def test_each_declaration_gets_its_own_block(self):
        out = _render(_media_hosts=[
            HOST,
            {"hostname": "thumbs.example.com", "bucket": "bucket-b",
             "endpoint": "region.provider.com"},
        ])
        assert "images.example.com {" in out and "thumbs.example.com {" in out
        assert out.count("media.log") == 2
        assert "header_up Host bucket-b.region.provider.com" in out


class TestFailsClosed:
    def test_missing_required_fields_are_rejected(self):
        msgs = [str((t.get("ansible.builtin.fail") or {}).get("msg", ""))
                for t in _tasks()]
        assert any("is missing" in m and "hostname, bucket and endpoint" in m
                   for m in msgs)

    def test_a_hostname_already_in_caddyfile_global_is_refused(self):
        """Caddy will not load a config claiming one hostname twice, so an
        operator mid-migration would get a caddy that does not start."""
        msgs = [str((t.get("ansible.builtin.fail") or {}).get("msg", ""))
                for t in _tasks()]
        assert any("claims a hostname twice" in m for m in msgs)


class TestShippedScaffold:
    """The schema is otherwise discoverable only from documentation, so the
    file ships with the instance — commented, empty, and no-clobber."""

    SCAFFOLD = os.path.join(
        REPO_ROOT, "instance_template", "config", "media-hosts.yaml")
    INIT_CONFIG = os.path.join(
        REPO_ROOT, "roles", "orchestrator", "tasks", "init_config.yml")

    def test_scaffold_exists_and_renders_nothing(self):
        with open(self.SCAFFOLD) as f:
            doc = yaml.safe_load(f)
        assert (doc or {}).get("media_hosts") == [], (
            "the shipped file must declare no hosts, or every new instance "
            "would render media blocks it never asked for"
        )

    def test_scaffold_documents_the_required_fields(self):
        src = open(self.SCAFFOLD).read()
        for field in ("hostname", "bucket", "endpoint"):
            assert field in src

    def test_copied_no_clobber_from_create_and_upgrade(self):
        """force: false matters: init_config runs on upgrade too, so a
        clobbering copy would erase an operator's declarations."""
        with open(self.INIT_CONFIG) as f:
            tasks = list(_walk_tasks(yaml.safe_load(f)))
        copies = [t.get("ansible.builtin.copy") for t in tasks
                  if isinstance(t.get("ansible.builtin.copy"), dict)]
        mh = [c for c in copies if "media-hosts.yaml" in str(c.get("dest", ""))]
        assert mh, "media-hosts.yaml is not scaffolded by init_config"
        assert mh[0].get("force") is False
