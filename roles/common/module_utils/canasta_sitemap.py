# -*- coding: utf-8 -*-
"""Sitemap URL path and identifier rules, shared by config validation, the
Caddyfile render, the HAProxy edge render and canasta_wikis_yaml.

The image's background generator publishes each wiki's sitemaps at
MW_SITEMAP_URL_PATH, relative to the wiki's URL, while the files stay in
public_assets/<wiki>/sitemap, which the image serves at
<wiki path>/public_assets/sitemap. A different URL path is mapped onto
that one in front of the image.
"""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import re

URL_PATH_KEY = "MW_SITEMAP_URL_PATH"
DEFAULT_URL_PATH = "/public_assets/sitemap"
MAX_URL_PATH_LENGTH = 100

_URL_PATH_RE = re.compile(r"(/[A-Za-z0-9_~-][A-Za-z0-9._~-]*)+")
# The image's own rule: the identifier becomes part of file names.
_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")


def url_path_error(value):
    """None if value is a usable MW_SITEMAP_URL_PATH, else why not."""
    if value == DEFAULT_URL_PATH:
        return None
    if (len(value) > MAX_URL_PATH_LENGTH or ".." in value
            or not _URL_PATH_RE.fullmatch(value)):
        return (
            "%s must be a URL path of at most %d characters that starts with "
            "'/', such as /wiki/sitemap: letters, digits and '-', '_', '.', "
            "'~' between slashes, no segment starting with '.', and no "
            "trailing slash. Got '%s'." % (URL_PATH_KEY, MAX_URL_PATH_LENGTH, value))
    for reserved in ("/w", "/public_assets"):
        if value == reserved or value.startswith(reserved + "/"):
            return ("%s cannot be %s or a path under it, which MediaWiki's "
                    "own files use. Got '%s'." % (URL_PATH_KEY, reserved, value))
    if value == "/wiki":
        return ("%s cannot be /wiki, where MediaWiki serves its pages. "
                "Got '%s'." % (URL_PATH_KEY, value))
    return None


def identifier_error(value):
    """None if value is a usable sitemap_identifier, else why not."""
    if _IDENTIFIER_RE.fullmatch(value or ""):
        return None
    return ("sitemap identifier '%s' may contain only letters, digits, "
            "underscores, hyphens and periods, and must start with a letter "
            "or digit." % value)


def wiki_identifier(wiki):
    """The identifier that names a wiki's sitemap files."""
    value = wiki.get("sitemap_identifier")
    return str(value) if value is not None else str(wiki.get("id", ""))


def rewrites(wikis, url_path):
    """[{host, path, pattern, target}] mapping each wiki's configured
    sitemap URL path (path, matched by the regular expression pattern) onto
    the one the image serves its sitemap files at (target). Empty when the
    URL path is unset or the image's own."""
    if not url_path or url_path == DEFAULT_URL_PATH:
        return []
    out = []
    for wiki in wikis or []:
        host, _, path = str(wiki.get("url") or "").partition("/")
        host = host.split(":", 1)[0].lower()
        if not host:
            continue
        prefix = "/" + path.strip("/") if path.strip("/") else ""
        out.append({"host": host, "path": prefix + url_path,
                    "pattern": "^%s/" % re.escape(prefix + url_path),
                    "target": prefix + DEFAULT_URL_PATH})
    return sorted(out, key=lambda r: (-len(r["path"]), r["host"], r["path"]))
