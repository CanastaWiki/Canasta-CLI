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
        assert any("has no" in m and "hostname, bucket and endpoint" in m
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


class TestGitopsTemplating:
    """Media hostnames and buckets are per-host, exactly as wiki URLs are.

    A single shared file gives every host every other environment's blocks.
    So config/media-hosts.yaml is rendered from media-hosts.yaml.template,
    joining .env, wikis.yaml and my.cnf as a per-host rendered file.
    """

    RENDER = os.path.join(
        REPO_ROOT, "roles", "gitops", "tasks", "_render_media_hosts.yml")
    RENDER_COMPOSE = os.path.join(
        REPO_ROOT, "roles", "gitops", "tasks", "render_compose.yml")
    PULL_COMPOSE = os.path.join(
        REPO_ROOT, "roles", "gitops", "tasks", "pull_compose.yml")
    GITIGNORE = os.path.join(
        REPO_ROOT, "roles", "gitops", "files", "gitignore.default")

    def _include(self, path):
        with open(path) as f:
            for t in _walk_tasks(yaml.safe_load(f)):
                inc = t.get("ansible.builtin.include_tasks")
                target = inc.get("file", "") if isinstance(inc, dict) else str(inc or "")
                if "_render_media_hosts.yml" in target:
                    return t
        return None

    def _rules(self):
        with open(self.GITIGNORE) as f:
            return [ln.strip() for ln in f
                    if ln.strip() and not ln.strip().startswith("#")]

    def test_rendered_by_render_compose(self):
        assert self._include(self.RENDER_COMPOSE) is not None

    def test_rendered_by_pull_compose(self):
        """pull_compose does not delegate to render_compose — each renders
        independently, so a change to one silently misses the other."""
        assert self._include(self.PULL_COMPOSE) is not None

    def test_each_path_passes_its_own_vars(self):
        """render_compose builds _render_vars, pull_compose builds _pull_vars.
        Passing the wrong one renders every placeholder empty."""
        assert "_render_vars" in str(self._include(self.RENDER_COMPOSE)["vars"])
        assert "_pull_vars" in str(self._include(self.PULL_COMPOSE)["vars"])

    def test_rendered_output_is_gitignored(self):
        """Generated per host now, so tracking it would reintroduce the shared
        file this change exists to remove."""
        assert "config/media-hosts.yaml" in self._rules()

    def test_the_template_itself_is_not_ignored(self):
        assert "media-hosts.yaml.template" not in self._rules()

    def test_refuses_a_hostname_with_no_bucket_or_endpoint(self):
        """A hostname whose bucket or endpoint is empty is a broken entry —
        rendering it would point a live hostname at the wrong upstream."""
        with open(self.RENDER) as f:
            tasks = list(_walk_tasks(yaml.safe_load(f)))
        msgs = [str((t.get("ansible.builtin.fail") or {}).get("msg", ""))
                for t in tasks]
        assert any("Refusing to write config/media-hosts.yaml" in m
                   and "no" in m and "bucket or endpoint" in m for m in msgs)

    def test_an_entry_with_no_hostname_is_skipped_not_fatal(self):
        """`gitops join` rebuilds hosts/<host>/vars.yaml from the instance's
        .env, dropping media vars prepared for that host in the repo. Failing
        there makes the join impossible; the entry simply is not served yet.
        """
        with open(self.RENDER) as f:
            tasks = list(_walk_tasks(yaml.safe_load(f)))
        names = [t.get("name", "") for t in tasks]
        assert any("does not serve" in n for n in names), (
            "an unset entry must be reported, not fatal"
        )
        # and the write must use the filtered list, so no empty block remains
        writes = [t for t in tasks
                  if isinstance(t.get("ansible.builtin.copy"), dict)
                  and "media-hosts.yaml" in str(t["ansible.builtin.copy"].get("dest"))]
        assert writes and "_mh_complete" in str(writes[0]["ansible.builtin.copy"])

    def test_empty_field_detection_parses_yaml_rather_than_matching_text(self):
        """Text-matching the bare "key:" a blank value leaves behind needs a
        newline split, and inside a folded scalar the '\n' is read literally —
        the expression then yields nothing and the guard never fires. It also
        misses the first field of a list entry, which carries a "- " prefix.
        """
        with open(self.RENDER) as f:
            tasks = list(_walk_tasks(yaml.safe_load(f)))
        guard = [t for t in tasks
                 if "Sort media hosts into complete" in t.get("name", "")]
        assert guard, "the entry-sorting task is missing"
        expr = str(guard[0].get("ansible.builtin.set_fact"))
        assert "rejectattr" in expr, "the guard must inspect parsed entries"
        assert "split(" not in expr, (
            "a newline split inside a folded scalar is read literally, so the "
            "expression silently yields nothing and the guard never fires"
        )
        parse = [t for t in tasks
                 if "Parse the rendered media hosts" in t.get("name", "")]
        assert parse and "from_yaml" in str(parse[0])
