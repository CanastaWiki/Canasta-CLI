"""Clients can neither purge Varnish nor evict its objects.

Varnish admits PURGE from private addresses so MediaWiki's purges pass on
both orchestrators, and every client request reaches Varnish from Caddy,
whose address is private. The generated Caddyfile therefore refuses PURGE.
A client `Cache-Control: no-cache` must not reach the cache logic: the old
`ban(req.url)` was a VCL error on Varnish 7, and forcing a miss instead would
let any client send every request through to MediaWiki.
"""

import os
import re

import jinja2
import pytest

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
CADDYFILE_J2 = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "templates", "Caddyfile.j2")
VCL = os.path.join(REPO_ROOT, "instance_template", "config", "default.vcl")

PURGE_BLOCK = "    @purge method PURGE\n    handle @purge {\n        respond 405\n    }\n"


def _render(**variables):
    with open(CADDYFILE_J2) as f:
        source = f.read()
    env = jinja2.Environment(undefined=jinja2.ChainableUndefined)
    base = {
        "_site_address": "wiki.example.com",
        "_backend": "varnish:80",
        "_observable": False,
        "_media_hosts": [{"hostname": "media.example.com",
                          "bucket": "b", "endpoint": "s3.example.com"}],
    }
    base.update(variables)
    return env.from_string(source).render(**base)


def _block(text, address):
    start = text.index(address + " {")
    return text[start:text.index("\n}\n", start)]


@pytest.mark.parametrize("observable", [False, True])
def test_site_block_refuses_purge_before_proxying(observable):
    site = _block(_render(_observable=observable, _os_user="u",
                          _os_password_hash="h"), "wiki.example.com")
    assert PURGE_BLOCK in site
    assert site.index(PURGE_BLOCK) < site.index("reverse_proxy")
    if observable:
        assert site.index(PURGE_BLOCK) < site.index("handle @opensearch")


def test_media_host_block_has_no_purge_rule():
    media = _block(_render(), "media.example.com")
    assert "PURGE" not in media


def _vcl():
    with open(VCL) as f:
        return f.read()


def test_client_cache_control_does_not_affect_caching():
    vcl = _vcl()
    assert not re.search(r"^\s*ban\(", vcl, re.M)
    assert "hash_always_miss" not in vcl
    assert "req.http.Cache-Control" not in vcl


def test_purge_acl_still_admits_internal_purges():
    vcl = _vcl()
    acl = vcl[vcl.index("acl purge {"):]
    acl = acl[:acl.index("}")]
    for cidr in ('"10.0.0.0"/8', '"172.16.0.0"/12', '"192.168.0.0"/16'):
        assert cidr in acl
    assert "return (purge);" in vcl


UPGRADE = os.path.join(REPO_ROOT, "roles", "upgrade", "tasks", "main.yml")


def test_upgrade_rerenders_and_reloads_the_caddyfile():
    import yaml

    with open(UPGRADE) as f:
        tasks = yaml.safe_load(f)
    names = [t.get("name") for t in tasks]
    regen = tasks[names.index("Regenerate the Caddyfile")]
    assert regen["ansible.builtin.include_role"] == {
        "name": "orchestrator", "tasks_from": "update_config.yml"}
    assert "when" not in regen
    reload_ = tasks[names.index("Reload Caddy with the regenerated Caddyfile")]
    assert reload_["ansible.builtin.include_tasks"].strip().endswith(
        "roles/orchestrator/tasks/reload_caddy.yml")
    assert names.index("Regenerate the Caddyfile") \
        < names.index("Decide whether a restart is needed") \
        < names.index("Reload Caddy with the regenerated Caddyfile")
