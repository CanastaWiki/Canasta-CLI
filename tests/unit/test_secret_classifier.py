"""The canonical secret classifier is the single source of truth for
"this key is a secret". These tests lock the full secret surface (so a
future key can't silently leak) and the non-secret exclusions."""
import os
import re

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CLASSIFIER = os.path.join(REPO_ROOT, "vars", "secret_classification.yml")


def _load():
    with open(CLASSIFIER) as fh:
        return yaml.safe_load(fh)


def _secret_regex(cls):
    # Mirror canasta_secret_key_regex's Jinja construction so the test checks
    # the same effective pattern the playbook uses.
    prefixes = cls["canasta_secret_prefixes"] + cls["canasta_secret_explicit"]
    return "^([^=]*" + cls["canasta_secret_key_pattern"] + "|" + "|".join(prefixes) + ")"


# Genuine secrets — must ALL classify as secret (kept out of ConfigMaps/gitops).
SECRETS = [
    "MYSQL_PASSWORD", "MYSQL_ROOT_PASSWORD", "WIKI_DB_PASSWORD", "MW_SECRET_KEY",
    "RESTIC_PASSWORD", "RESTIC_REPOSITORY", "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY", "AZURE_ACCOUNT_KEY", "B2_APPLICATION_KEY",
    "RCLONE_CONFIG_REMOTE_PASS", "SMTP_PASSWORD", "SMTP_USER",
    "CROWDSEC_BOUNCER_API_KEY", "SOME_VENDOR_TOKEN", "PARTNER_CREDENTIAL",
]

# Operational config — must NOT be suppressed/stripped (stays cleartext).
NON_SECRETS = [
    "MW_SITE_SERVER", "MW_SITE_FQDN", "HTTP_PORT", "HTTPS_PORT",
    "CADDY_AUTO_HTTPS", "CANASTA_ENABLE_CROWDSEC", "PHP_UPLOAD_MAX_FILESIZE",
    "MYSQL_HOST", "MYSQL_USER", "MW_SITEMAP_PAUSE_DAYS",
]


def test_classifier_parses_and_defines_the_pieces():
    cls = _load()
    for key in ("canasta_secret_key_pattern", "canasta_secret_prefixes",
                "canasta_secret_explicit", "canasta_backup_backend_prefixes",
                "canasta_host_specific_nonsecret"):
        assert key in cls, f"{key} missing from the classifier"


def test_all_secrets_classified_as_secret():
    regex = _secret_regex(_load())
    for key in SECRETS:
        assert re.match(regex, key), f"{key} must classify as secret"
        # Same when applied to a `KEY=value` .env line (only the key inspected).
        assert re.match(regex, key + "=somevalue"), f"{key}= line must match"


def test_non_secrets_not_classified():
    regex = _secret_regex(_load())
    for key in NON_SECRETS:
        assert not re.match(regex, key), f"{key} must NOT classify as secret"


def test_rclone_covered_but_smtp_excluded_in_backup_backends():
    cls = _load()
    # RCLONE_ was the historical omission that broke restic rclone on K8s.
    assert "RCLONE_" in cls["canasta_backup_backend_prefixes"]
    # SMTP is a secret but not a backup backend, so it stays out of backup-env.
    assert "SMTP_" not in cls["canasta_backup_backend_prefixes"]
