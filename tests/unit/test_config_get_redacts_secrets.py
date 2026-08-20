"""`canasta config get` must not print credentials it was not asked for.

Reading configuration is routine, read-only and unconfirmed, so its
output ends up in scrollbacks, CI logs and support threads. A secret
leaks by being shown. Naming a key is an explicit request and still
prints; the bare dump is not, and masks.
"""

import os
import sys

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

import direct_commands  # noqa: E402
import direct_commands._helpers  # noqa: E402

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
CLASSIFICATION = os.path.join(
    REPO_ROOT, "vars", "secret_classification.yml")

ENV = {
    "MYSQL_PASSWORD": "s3cr3t-root-pw",
    "MW_SECRET_KEY": "deadbeef" * 8,
    "RESTIC_PASSWORD": "repo-pw",
    "RESTIC_REPOSITORY": "s3:s3.amazonaws.com/bucket",
    "AWS_SECRET_ACCESS_KEY": "aws-secret",
    "SMTP_PASSWORD": "mail-pw",
    "OS_PASSWORD_HASH": "$2y$hash",
    "MYSQL_HOST": "db",
    "HTTP_PORT": "80",
    "COMPOSE_PROFILES": "varnish,internal-db",
}


def _run(monkeypatch, keys=None, show_secrets=False):
    monkeypatch.setattr(
        direct_commands._helpers, "_resolve_instance",
        lambda args: ("test", {"path": "/srv/test", "host": "localhost"}))
    monkeypatch.setattr(
        direct_commands._helpers, "_read_env_file", lambda *a: dict(ENV))
    monkeypatch.setattr(
        direct_commands._helpers, "_read_env_content", lambda *a: "")
    args = type("Args", (), {
        "id": "test", "keys": keys or [], "show_secrets": show_secrets})()
    return direct_commands.cmd_config_get(args)


class TestBareDump:
    def test_credentials_are_masked(self, monkeypatch, capsys):
        assert _run(monkeypatch) == 0
        out = capsys.readouterr().out
        for key, value in ENV.items():
            if key in ("MYSQL_HOST", "HTTP_PORT", "COMPOSE_PROFILES"):
                continue
            assert value not in out, "%s leaked into the listing" % key
            assert "%s=********" % key in out

    def test_non_secrets_are_untouched(self, monkeypatch, capsys):
        _run(monkeypatch)
        out = capsys.readouterr().out
        assert "MYSQL_HOST=db" in out
        assert "HTTP_PORT=80" in out
        assert "COMPOSE_PROFILES=varnish,internal-db" in out

    def test_it_says_how_to_read_a_masked_value(self, monkeypatch, capsys):
        _run(monkeypatch)
        err = capsys.readouterr().err
        assert "masked" in err and "--show-secrets" in err

    def test_a_repository_url_counts_as_secret(self, monkeypatch, capsys):
        # It can embed credentials, which is why the classifier lists it
        # explicitly rather than relying on the word pattern.
        _run(monkeypatch)
        assert "s3.amazonaws.com/bucket" not in capsys.readouterr().out


class TestExplicitRequest:
    def test_a_named_key_prints_its_value(self, monkeypatch, capsys):
        assert _run(monkeypatch, keys=["MYSQL_PASSWORD"]) == 0
        assert "MYSQL_PASSWORD=s3cr3t-root-pw" in capsys.readouterr().out

    def test_show_secrets_unmasks_the_listing(self, monkeypatch, capsys):
        _run(monkeypatch, show_secrets=True)
        out = capsys.readouterr().out
        assert "MYSQL_PASSWORD=s3cr3t-root-pw" in out
        assert "********" not in out


class TestClassificationMatchesAnsible:
    """vars/secret_classification.yml calls itself the ONE definition. The
    CLI reads it rather than restating it, so this pins that it is really
    being read — a second, quieter opinion is the failure mode."""

    def _yaml(self):
        with open(CLASSIFICATION) as f:
            return yaml.safe_load(f)

    def test_every_declared_prefix_is_treated_as_secret(self):
        for prefix in self._yaml()["canasta_secret_prefixes"]:
            assert direct_commands._helpers._is_secret_key(prefix + "ANYTHING")

    def test_every_explicit_key_is_treated_as_secret(self):
        for key in self._yaml()["canasta_secret_explicit"]:
            assert direct_commands._helpers._is_secret_key(key)

    def test_every_pattern_word_is_treated_as_secret(self):
        pattern = self._yaml()["canasta_secret_key_pattern"]
        for word in pattern.strip("()").split("|"):
            assert direct_commands._helpers._is_secret_key("MY_" + word)

    def test_ordinary_keys_are_not(self):
        for key in ("MYSQL_HOST", "HTTP_PORT", "COMPOSE_PROFILES",
                    "MW_SITE_SERVER", "CANASTA_ENABLE_VARNISH"):
            assert not direct_commands._helpers._is_secret_key(key)

    def test_an_empty_value_is_not_masked_into_looking_set(self):
        # Masking "" would make an unset key look configured.
        assert direct_commands._helpers.redact("MYSQL_PASSWORD", "") == ""
