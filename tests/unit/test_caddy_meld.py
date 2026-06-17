"""Unit tests for the Caddyfile global-block melder (filter_plugins/canasta_caddy.py).

Caddy permits exactly one global options block, first. The melder collapses
every top-level global block into one (placed first) while leaving site blocks
and snippets in order — and must not miscount braces inside comments, strings,
heredocs, or {placeholders}.
"""

import os
import sys

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "..", "filter_plugins")
)
from canasta_caddy import meld_caddy_global_blocks, _parse_top_level  # noqa: E402


def _count_global_blocks(text):
    blocks, _ = _parse_top_level(text)
    return sum(1 for b in blocks if b["is_global"])


def _site_labels(text):
    blocks, _ = _parse_top_level(text)
    return [b["label"].strip() for b in blocks if not b["is_global"]]


class TestMeld:
    def test_two_globals_merge_first(self):
        out = meld_caddy_global_blocks(
            "{\n    email a@b.com\n}\n"
            "example.com {\n    reverse_proxy x\n}\n"
            "{\n    debug\n}\n"
        )
        assert _count_global_blocks(out) == 1
        assert "email a@b.com" in out
        assert "debug" in out
        # the merged global precedes the site block
        assert out.index("email a@b.com") < out.index("example.com {")
        assert _site_labels(out) == ["example.com"]

    def test_global_plus_site(self):
        out = meld_caddy_global_blocks(
            "{\n    debug\n}\nexample.com {\n    reverse_proxy x\n}\n"
        )
        assert _count_global_blocks(out) == 1
        assert "reverse_proxy x" in out

    def test_only_site_blocks_no_global_added(self):
        out = meld_caddy_global_blocks("example.com {\n    reverse_proxy x\n}\n")
        assert _count_global_blocks(out) == 0
        assert _site_labels(out) == ["example.com"]

    def test_single_global_preserved_and_first(self):
        out = meld_caddy_global_blocks("{\n    email a@b.com\n}\n")
        assert _count_global_blocks(out) == 1
        assert "email a@b.com" in out

    def test_global_after_site_is_moved_first(self):
        # A global block illegally placed after a site block gets relocated.
        out = meld_caddy_global_blocks(
            "example.com {\n    reverse_proxy x\n}\n{\n    debug\n}\n"
        )
        assert _count_global_blocks(out) == 1
        assert out.index("debug") < out.index("example.com {")

    def test_braces_in_quoted_string_not_counted(self):
        out = meld_caddy_global_blocks(
            '{\n    debug\n}\n'
            'example.com {\n    respond "a } brace { in a string"\n}\n'
        )
        assert _count_global_blocks(out) == 1
        assert _site_labels(out) == ["example.com"]
        assert 'respond "a } brace { in a string"' in out

    def test_braces_in_heredoc_not_counted(self):
        src = (
            "{\n    debug\n}\n"
            "example.com {\n"
            "    respond <<HTML\n"
            "    <div>{ not a block }</div>\n"
            "    HTML\n"
            "}\n"
        )
        out = meld_caddy_global_blocks(src)
        assert _count_global_blocks(out) == 1
        assert _site_labels(out) == ["example.com"]
        assert "{ not a block }" in out

    def test_brace_in_comment_not_counted(self):
        out = meld_caddy_global_blocks(
            "{\n    debug\n}\n"
            "example.com {\n    # a } stray { brace in a comment\n"
            "    reverse_proxy x\n}\n"
        )
        assert _count_global_blocks(out) == 1
        assert _site_labels(out) == ["example.com"]

    def test_placeholder_braces_not_counted(self):
        out = meld_caddy_global_blocks(
            "{\n    crowdsec {\n        api_key {env.KEY}\n    }\n}\n"
            "example.com {\n    reverse_proxy x\n}\n"
        )
        assert _count_global_blocks(out) == 1
        assert "api_key {env.KEY}" in out
        assert _site_labels(out) == ["example.com"]

    def test_snippet_is_labeled_not_global(self):
        # `(name) { … }` is a snippet definition — a labeled block, not global.
        out = meld_caddy_global_blocks(
            "{\n    debug\n}\n"
            "(logging) {\n    log\n}\n"
            "example.com {\n    import logging\n}\n"
        )
        assert _count_global_blocks(out) == 1
        assert "(logging)" in out
        assert set(_site_labels(out)) == {"(logging)", "example.com"}

    def test_realistic_cli_plus_user_global(self):
        # Mirrors the generated CLI global block + an inlined user global block.
        cli = (
            "{\n"
            "    servers {\n"
            "        client_ip_headers Cf-Connecting-Ip\n"
            "        trusted_proxies cdn_ranges {\n"
            "            interval 12h\n"
            "            provider cloudflare\n"
            "        }\n"
            "    }\n"
            "    order crowdsec first\n"
            "    crowdsec {\n"
            "        api_url http://crowdsec:8080\n"
            "        api_key {env.CROWDSEC_BOUNCER_API_KEY}\n"
            "    }\n"
            "}\n"
        )
        user = "{\n    email admin@example.com\n}\n"
        site = "conservation-wiki.com {\n    reverse_proxy varnish:80\n}\n"
        out = meld_caddy_global_blocks(cli + user + site)
        assert _count_global_blocks(out) == 1
        for needle in ("order crowdsec first", "trusted_proxies cdn_ranges",
                       "api_key {env.CROWDSEC_BOUNCER_API_KEY}",
                       "email admin@example.com"):
            assert needle in out
        assert _site_labels(out) == ["conservation-wiki.com"]
        # global precedes the site
        assert out.index("order crowdsec first") < out.index("conservation-wiki.com {")

    def test_empty_input(self):
        assert meld_caddy_global_blocks("").strip() == ""
        assert meld_caddy_global_blocks(None).strip() == ""

    def test_comments_outside_blocks_preserved(self):
        out = meld_caddy_global_blocks(
            "# header\n{\n    debug\n}\nexample.com {\n    reverse_proxy x\n}\n"
        )
        assert "# header" in out
        assert _count_global_blocks(out) == 1

    def test_crlf_normalized_no_spurious_blank(self):
        out = meld_caddy_global_blocks(
            "{\r\n    debug\r\n}\r\nexample.com {\r\n    reverse_proxy x\r\n}\r\n"
        )
        assert "\r" not in out
        assert _count_global_blocks(out) == 1
        # no spurious blank line right after the merged opener
        assert "{\n\n" not in out

    def test_jinja_braces_passed_through(self):
        # The melder is pure string work — a user's literal {{ … }} survives;
        # render-time safety is the `| ansible.builtin.unsafe` in rewrite_caddy.
        out = meld_caddy_global_blocks(
            '{\n    debug\n}\nexample.com {\n    respond "{{ not_evaluated }}"\n}\n'
        )
        assert "{{ not_evaluated }}" in out
        assert _count_global_blocks(out) == 1
