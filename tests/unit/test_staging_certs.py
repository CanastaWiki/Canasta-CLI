"""Tests for the --staging-certs / CANASTA_STAGING_CERTS feature.

Covers both orchestrators:
- Compose (Caddyfile.j2 emits acme_ca, rewrite_caddy.yml reads .env)
- Kubernetes (values.yaml template, cert-manager issuers, side-effect
  propagation, TLS cascade)

Rendering tests evaluate the real Jinja templates against different
inputs so a flipped ternary or wrong field name is caught — something
structural string-greps wouldn't detect.
"""

import os
import re

import jinja2
import yaml


REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
HELM_CHART = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "files", "helm", "canasta",
)


def _ansible_jinja_env():
    """Return a Jinja2 Environment with minimal Ansible-compatible filters
    (ternary, regex_replace, bool) registered. Used by rendering tests
    that need to execute an Ansible-style template without spinning up
    full Ansible."""
    env = jinja2.Environment(
        undefined=jinja2.StrictUndefined,
        keep_trailing_newline=True,
    )
    env.filters["ternary"] = lambda cond, a, b: a if cond else b
    env.filters["regex_replace"] = lambda s, pat, repl="": re.sub(pat, repl, str(s))
    env.filters["bool"] = lambda v: str(v).lower() in ("true", "1", "yes", "y")
    return env


class TestStagingCerts:
    """CANASTA_STAGING_CERTS plumbing across create, both
    orchestrators' templates, and post-create config set."""

    # --- Create time: the flag must land in .env ---

    def test_create_writes_canasta_staging_certs_env(self):
        """The create role must persist the --staging-certs flag as
        CANASTA_STAGING_CERTS in .env so both orchestrators' template
        logic can read it. The task may live in main.yml or a sub-
        task file under roles/create/tasks/."""
        tasks_dir = os.path.join(
            REPO_ROOT, "roles", "create", "tasks",
        )
        combined = []
        for fname in sorted(os.listdir(tasks_dir)):
            if fname.endswith(".yml"):
                with open(os.path.join(tasks_dir, fname)) as f:
                    combined.append(f.read())
        content = "\n".join(combined)
        assert "key: CANASTA_STAGING_CERTS" in content, (
            "the create role must write CANASTA_STAGING_CERTS to .env"
        )
        assert "staging_certs | default(false)" in content, (
            "The .env value must be derived from the staging_certs flag"
        )

    # --- Compose: rewrite_caddy.yml must read the .env key ---

    def test_staging_certs_is_a_known_config_key(self):
        path = os.path.join(
            REPO_ROOT, "roles", "config", "defaults", "main.yml",
        )
        with open(path) as f:
            defaults = yaml.safe_load(f)
        # canasta_known_keys is a list of dicts ({name, group,
        # description, default}); project names to check membership.
        names = [e["name"] for e in defaults["canasta_known_keys"]]
        assert "CANASTA_STAGING_CERTS" in names, (
            "CANASTA_STAGING_CERTS must be in canasta_known_keys so "
            "'canasta config set CANASTA_STAGING_CERTS=...' doesn't "
            "require --force"
        )
