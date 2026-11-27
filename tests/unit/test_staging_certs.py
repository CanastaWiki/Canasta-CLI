"""Tests for the --staging-certs / CANASTA_STAGING_CERTS feature."""

import os

import yaml


REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")


class TestStagingCerts:
    """CANASTA_STAGING_CERTS plumbing across create and post-create
    config set."""

    # --- Create time: the flag must land in .env ---

    def test_create_writes_canasta_staging_certs_env(self):
        """The create role must persist the --staging-certs flag as
        CANASTA_STAGING_CERTS in .env. The task may live in main.yml or a
        sub-task file under roles/create/tasks/."""
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
