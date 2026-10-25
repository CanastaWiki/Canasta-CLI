# Jinja filters for the sitemap URL path and identifier.
#
# Loaded via the `filter_plugins` path in ansible.cfg (role-local plugin
# auto-discovery does not fire under include_role / include_tasks).

import importlib.util
import os


def _load_rules():
    # Controller-side plugins cannot import ansible.module_utils.*, so the
    # shared rules are loaded from their file.
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                        "roles", "common", "module_utils", "canasta_sitemap.py")
    spec = importlib.util.spec_from_file_location(
        "_canasta_sitemap_for_filters", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_rules = _load_rules()


def sitemap_url_path_error(value):
    """'' for a usable MW_SITEMAP_URL_PATH (or none), else why not."""
    if value is None or value == "":
        return ""
    return _rules.url_path_error(str(value)) or ""


def sitemap_identifier_error(value):
    """'' for a usable sitemap identifier, else why not."""
    return _rules.identifier_error(str(value)) or ""


def sitemap_identifier(wiki):
    """The identifier that names a wiki's sitemap files."""
    return _rules.wiki_identifier(wiki)


def sitemap_rewrites(wikis, url_path):
    """The Caddy rewrites that serve each wiki's sitemaps at url_path."""
    return _rules.rewrites(wikis, url_path or "")


class FilterModule(object):
    def filters(self):
        return {
            "sitemap_url_path_error": sitemap_url_path_error,
            "sitemap_identifier_error": sitemap_identifier_error,
            "sitemap_identifier": sitemap_identifier,
            "sitemap_rewrites": sitemap_rewrites,
        }
