"""Site addresses in a Caddyfile, as the HAProxy edge reads them.

Site blocks in a user's Caddyfile.global (a www redirect, say) serve
hostnames the edge must route and obtain certificates for, and behind the
edge Caddy must serve them over plain HTTP.
"""

import canasta_caddyfile as cf

GLOBAL = """
{
    email admin@example.com
}

(common) {
    header X-Frame-Options DENY
}

www.example.com, https://alias.example.com {
    redir https://example.com{uri} 301
}

http://plain.example.com {
    respond "ok"
}

*.example.org, :8080, example.net:8443, 192.0.2.1, localhost {
    respond "skipped"
}

Docs.Example.com:443/path/* {
    respond "docs"
}
"""


def test_routable_hostnames_and_skips():
    hosts, skipped = cf.site_hostnames(GLOBAL)
    assert hosts == ["www.example.com", "alias.example.com", "plain.example.com",
                     "docs.example.com"]
    assert dict(skipped) == {
        "*.example.org": "it is a wildcard",
        ":8080": "it has no hostname",
        "example.net:8443": "it names port 8443",
        "192.0.2.1": "it is not a hostname",
        "localhost": "it is not a hostname",
    }


def test_global_options_and_snippets_are_not_sites():
    hosts, skipped = cf.site_hostnames("{\n  debug\n}\n(snip) {\n  respond x\n}\n")
    assert hosts == [] and skipped == []


def test_routed_addresses_become_http():
    hosts, _ = cf.site_hostnames(GLOBAL)
    out = cf.http_site_addresses(GLOBAL, hosts)
    assert "http://www.example.com, http://alias.example.com {" in out
    assert "http://plain.example.com {" in out
    assert "http://docs.example.com/path/* {" in out
    assert "*.example.org, :8080, example.net:8443" in out
    assert "email admin@example.com" in out and "(common) {" in out
    assert out.count("{") == GLOBAL.count("{")


def test_unrouted_text_is_unchanged():
    assert cf.http_site_addresses(GLOBAL, []) == GLOBAL


def test_placeholders_in_addresses_are_part_of_the_address():
    text = ("{$SITE_HOST}, www.example.com {\n    respond x\n}\n"
            "{\n    email a@example.com\n}\n")
    blocks, _ = cf.parse_top_level(text)
    labels = [b["label"].strip() for b in blocks]
    assert labels == ["{$SITE_HOST}, www.example.com", ""]
    assert [b["is_global"] for b in blocks] == [False, True]
    hosts, skipped = cf.site_hostnames(text)
    assert hosts == ["www.example.com"]
    assert skipped == [("{$SITE_HOST}", "it uses a placeholder")]
    out = cf.http_site_addresses(text, hosts)
    assert "{$SITE_HOST}, http://www.example.com {" in out


def test_a_placeholder_inside_a_host_is_kept_with_it():
    hosts, skipped = cf.site_hostnames("wiki.{$DOMAIN} {\n    respond x\n}\n")
    assert hosts == [] and skipped == [("wiki.{$DOMAIN}", "it uses a placeholder")]
