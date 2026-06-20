"""Guards against the HTTP-only redirect bug in #666.

`canasta create` must derive the MW_SITE_SERVER scheme from
CADDY_AUTO_HTTPS (via the _http_only fact), not hardcode https://.
A hardcoded https:// gives an HTTP-only instance an https $wgServer,
so MediaWiki canonical-redirects login/Special pages to https on a
stack that serves no TLS -> SSL_PROTOCOL_ERROR.
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
ENV_UPDATE = os.path.join(
    REPO_ROOT, "roles", "create", "tasks", "_env_update.yml"
)
ENVFILE = os.path.join(
    REPO_ROOT, "roles", "create", "tasks", "_envfile.yml"
)
CREATE_MAIN = os.path.join(REPO_ROOT, "roles", "create", "tasks", "main.yml")


def _load_tasks(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _read(path):
    with open(path) as f:
        return f.read()


def _site_server_value():
    """Return the MW_SITE_SERVER value templated in the core .env loop."""
    for task in _load_tasks(ENV_UPDATE):
        for item in task.get("loop", []) or []:
            if isinstance(item, dict) and item.get("key") == "MW_SITE_SERVER":
                return item["value"]
    raise AssertionError("MW_SITE_SERVER not set in _env_update.yml core loop")


class TestSiteServerScheme:
    def test_scheme_is_not_hardcoded_https(self):
        value = _site_server_value()
        assert not value.lstrip().startswith("https://"), (
            "MW_SITE_SERVER must not hardcode https:// at create time; an "
            "HTTP-only instance would then redirect to https with no cert"
        )

    def test_scheme_derives_from_http_only(self):
        value = _site_server_value()
        assert "_http_only" in value, (
            "MW_SITE_SERVER scheme must derive from the _http_only fact"
        )
        assert "http" in value and "https" in value, (
            "MW_SITE_SERVER must choose between http and https schemes"
        )

    def test_http_only_is_set_before_env_update(self):
        """_env_update.yml relies on the _http_only fact; _envfile.yml must
        set it and run first in create's main.yml."""
        assert "_http_only" in _read(ENVFILE), (
            "_envfile.yml must set the _http_only fact"
        )
        main = _read(CREATE_MAIN)
        assert main.index("_envfile.yml") < main.index("_env_update.yml"), (
            "_envfile.yml must be included before _env_update.yml so "
            "_http_only is defined when MW_SITE_SERVER is built"
        )
