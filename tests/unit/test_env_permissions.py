""".env is readable by its owner alone.

It holds MYSQL_PASSWORD, RESTIC_PASSWORD, MW_SECRET_KEY and the CrowdSec
bouncer API key. The do-not-edit header task used to write it 0644, and
because that task is skipped once the header exists — and canasta_env
rewrites the file in place without touching its mode — an instance that
got 0644 kept it for the rest of its life.

So there are two things to hold: the mode a new instance is given, and
the migration that repairs an old one.
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
ENV_UPDATE = os.path.join(
    REPO_ROOT, "roles", "create", "tasks", "_env_update.yml")
UPGRADE_MAIN = os.path.join(REPO_ROOT, "roles", "upgrade", "tasks", "main.yml")
MIGRATION_NAME = "tighten_env_permissions.yml"
MIGRATION = os.path.join(
    REPO_ROOT, "roles", "upgrade", "tasks", "migrations", MIGRATION_NAME)

SECRET_KEYS = (
    "MYSQL_PASSWORD", "RESTIC_PASSWORD", "MW_SECRET_KEY",
    "CROWDSEC_BOUNCER_API_KEY",
)


def _tasks(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _find(items, predicate):
    for t in items or []:
        if not isinstance(t, dict):
            continue
        if predicate(t):
            return t
        for key in ("block", "always", "rescue"):
            if key in t:
                found = _find(t[key], predicate)
                if found:
                    return found
    return None


class TestNewInstancesGetOwnerOnlyEnv:
    def test_the_header_task_writes_0600(self):
        task = _find(
            _tasks(ENV_UPDATE),
            lambda t: t.get("name", "").startswith(
                "Prepend the do-not-edit header"),
        )
        assert task is not None, "the .env header task is gone or renamed"
        assert task["ansible.builtin.copy"]["mode"] == "0600", (
            "the header task rewrites .env, so its mode is the mode a new "
            "instance's credentials file ends up with"
        )

    def test_no_task_in_the_create_role_widens_env(self):
        """Any other writer of .env must not hand out group/other bits."""
        widened = []

        def visit(items):
            for t in items or []:
                if not isinstance(t, dict):
                    continue
                for module in ("ansible.builtin.copy", "ansible.builtin.file",
                               "ansible.builtin.template"):
                    spec = t.get(module)
                    if not isinstance(spec, dict):
                        continue
                    target = str(spec.get("dest") or spec.get("path") or "")
                    if not target.endswith("/.env"):
                        continue
                    mode = str(spec.get("mode", ""))
                    if mode and mode not in ("0600", "u=rw", "0400"):
                        widened.append((t.get("name"), mode))
                for key in ("block", "always", "rescue"):
                    if key in t:
                        visit(t[key])

        visit(_tasks(ENV_UPDATE))
        assert not widened, (
            ".env carries every instance secret; these tasks give it a "
            "wider mode: %s" % widened
        )


class TestExistingInstancesAreRepaired:
    def test_the_migration_exists_and_sets_0600(self):
        task = _find(
            _tasks(MIGRATION),
            lambda t: "ansible.builtin.file" in t,
        )
        assert task is not None, "the migration does not chmod anything"
        spec = task["ansible.builtin.file"]
        assert spec["path"].endswith("/.env")
        assert spec["mode"] == "0600"

    def test_the_migration_is_gated_on_the_probe(self):
        """A no-op upgrade must not chmod a file that is already correct."""
        task = _find(
            _tasks(MIGRATION), lambda t: "ansible.builtin.file" in t)
        assert "env_readable_beyond_owner" in str(task.get("when", "")), (
            "the chmod has to be conditional on the probe's finding, so a "
            "steady-state upgrade reports no change"
        )

    def test_the_migration_runs(self):
        """A migration file nothing includes is dead code."""
        task = _find(
            _tasks(UPGRADE_MAIN),
            lambda t: t.get("name") == "Run migrations",
        )
        assert task is not None, "the migration loop is gone or renamed"
        assert MIGRATION_NAME in task["loop"], (
            "%s is not in the upgrade's migration loop" % MIGRATION_NAME)


class TestTheSecretsThisProtects:
    def test_the_documented_secrets_are_real_env_keys(self):
        """If these move out of .env, this protection is aimed at nothing."""
        found = set()
        for root, _dirs, files in os.walk(os.path.join(REPO_ROOT, "roles")):
            for name in files:
                if not name.endswith((".yml", ".yaml", ".j2", ".py")):
                    continue
                with open(os.path.join(root, name), errors="ignore") as f:
                    text = f.read()
                for key in SECRET_KEYS:
                    if key in text:
                        found.add(key)
        assert found == set(SECRET_KEYS), (
            "these no longer appear anywhere in roles/: %s"
            % sorted(set(SECRET_KEYS) - found)
        )
