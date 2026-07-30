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
SETTINGS_FILES = os.path.join(
    REPO_ROOT, "roles", "create", "tasks", "_settings_files.yml"
)


def _load_tasks(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _read(path):
    with open(path) as f:
        return f.read()


def _http_only_expr():
    """Return the expression _envfile.yml assigns to the _http_only fact."""
    for task in _load_tasks(ENVFILE):
        fact = task.get("ansible.builtin.set_fact") or task.get("set_fact")
        if isinstance(fact, dict) and "_http_only" in fact:
            return fact["_http_only"]
    raise AssertionError("_envfile.yml does not set the _http_only fact")


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


class TestSkipTlsScheme:
    """Guards against #1338, the ordering variant of #666.

    #666's fix derives the scheme from _http_only, which _envfile.yml
    reads out of CADDY_AUTO_HTTPS. That works when the operator supplies
    CADDY_AUTO_HTTPS=off via --envfile, because the merge happens before
    the fact is set. It does NOT work for --skip-tls: _settings_files.yml
    writes CADDY_AUTO_HTTPS=off for the flag, and that runs after the
    scheme has already been decided. So --skip-tls produced an https://
    MW_SITE_SERVER against a Caddy serving plain HTTP.
    """

    def test_http_only_accounts_for_skip_tls(self):
        expr = _http_only_expr()
        assert "skip_tls" in expr, (
            "_http_only must account for --skip-tls directly; the flag's "
            "own CADDY_AUTO_HTTPS=off write happens later in create, so "
            "reading .env alone cannot see it"
        )
        assert "CADDY_AUTO_HTTPS" in expr, (
            "_http_only must still honor an operator-supplied "
            "CADDY_AUTO_HTTPS (e.g. via --envfile)"
        )

    def test_http_only_is_not_forced_by_orchestrator(self):
        """K8s must keep https://, or #1094 comes back.

        _settings_files.yml sets CADDY_AUTO_HTTPS=off for Kubernetes too,
        but there Caddy runs http-only behind an ingress that terminates
        TLS, so https:// is the correct $wgServer.
        """
        expr = _http_only_expr()
        for token in ("orchestrator", "kubernetes", "k8s"):
            assert token not in expr, (
                "_http_only must not key off the orchestrator: on K8s the "
                "ingress terminates TLS and MW_SITE_SERVER stays https"
            )

    def test_skip_tls_env_write_happens_after_the_scheme_is_derived(self):
        """Pins the ordering that makes the fix necessary.

        If a future change moves the CADDY_AUTO_HTTPS write before
        _env_update.yml, reading .env would suffice and this guard should
        be revisited rather than silently left in place.
        """
        main = _read(CREATE_MAIN)
        assert "skip_tls" in _read(SETTINGS_FILES), (
            "_settings_files.yml is expected to own the --skip-tls "
            "CADDY_AUTO_HTTPS write"
        )
        assert main.index("_env_update.yml") < main.index(
            "_settings_files.yml"
        ), (
            "_settings_files.yml still runs after _env_update.yml, so the "
            "--skip-tls flag must be read directly rather than via .env"
        )
