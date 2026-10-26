# -*- coding: utf-8 -*-
"""Sitemap URL path and identifier rules, shared by config validation,
canasta add, the Caddyfile render, the HAProxy edge render and
canasta_wikis_yaml.

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


def _wiki_location(wiki):
    """(host, path prefix) of a wiki's URL, as the rewrites match it."""
    host, _, path = str(wiki.get("url") or "").partition("/")
    path = path.strip("/")
    return host.split(":", 1)[0].lower(), "/" + path if path else ""


def _segments(path):
    return [s for s in path.split("/") if s]


def _farm_collisions(wikis, url_path):
    """For each wiki whose sitemap path is at, under or above another
    wiki's path on the same host: (wiki ID, its sitemap URL, whether it is
    inside the other wiki's path, the other wiki's ID, its URL). A URL
    belongs to the wiki with the longest path it falls under, so a wiki's
    sitemap path inside its own path or an enclosing wiki's path is fine."""
    if not url_path or url_path == DEFAULT_URL_PATH:
        return []
    located = [(str(w.get("id", "")),) + _wiki_location(w) for w in wikis or []]
    found = []
    for wiki_id, host, prefix in located:
        if not host:
            continue
        own = _segments(prefix)
        sitemap = _segments(prefix + url_path)
        for other_id, other_host, other_prefix in located:
            other = _segments(other_prefix)
            if other_id == wiki_id or other_host != host or not other:
                continue
            inside = (sitemap[:len(other)] == other
                      and own[:len(other)] != other)
            if inside or other[:len(sitemap)] == sitemap:
                found.append((wiki_id, host + prefix + url_path, inside,
                              other_id, host + other_prefix))
                break
    return found


def _relation(inside):
    return "inside the URL of" if inside else "which contains the URL of"


def farm_collision_error(wikis, url_path):
    """None unless url_path puts a wiki's sitemaps at, under or above
    another wiki's path on the same host, else why."""
    for wiki_id, at, inside, other_id, other_url in _farm_collisions(wikis, url_path):
        return ("%s=%s would publish the sitemaps of wiki '%s' at %s, %s wiki "
                "'%s' (%s)." % (URL_PATH_KEY, url_path, wiki_id, at,
                                _relation(inside), other_id, other_url))
    return None


def farm_collision_warnings(wikis, url_path, wiki_ids=None):
    """Why rewrites() leaves out each wiki it leaves out, or only those of
    wiki_ids."""
    return ["%s=%s: the sitemaps of wiki '%s' are not served at %s, %s wiki "
            "'%s' (%s)." % (URL_PATH_KEY, url_path, wiki_id, at,
                            ("which is " if inside else "") + _relation(inside),
                            other_id, other_url)
            for wiki_id, at, inside, other_id, other_url
            in _farm_collisions(wikis, url_path)
            if wiki_ids is None or wiki_id in wiki_ids]


def rewrites(wikis, url_path):
    """[{host, path, pattern, target}] mapping each wiki's configured
    sitemap URL path (path, matched by the regular expression pattern) onto
    the one the image serves its sitemap files at (target). Empty when the
    URL path is unset or the image's own. A wiki whose sitemap path overlaps
    another wiki's path is left out, so that path stays with that wiki."""
    if not url_path or url_path == DEFAULT_URL_PATH:
        return []
    overlapping = {c[0] for c in _farm_collisions(wikis, url_path)}
    out = []
    for wiki in wikis or []:
        host, prefix = _wiki_location(wiki)
        if not host or str(wiki.get("id", "")) in overlapping:
            continue
        out.append({"host": host, "path": prefix + url_path,
                    "pattern": "^%s/" % re.escape(prefix + url_path),
                    "target": prefix + DEFAULT_URL_PATH})
    return sorted(out, key=lambda r: (-len(r["path"]), r["host"], r["path"]))
