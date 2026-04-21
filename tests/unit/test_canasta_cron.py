"""Tests for the canasta-cron container service.

Covers:
- Dockerfile.cron exists and pins Supercronic by version + sha256
- CI workflow builds both images (canasta-ansible and canasta-cron)
- Compose stack declares the cron service under the 'cron' profile
- sync_compose_profiles honors CANASTA_ENABLE_CRON
- rewrite_cron.yml renders config/crontab from config/crontab.extras
- The crontab.j2 template produces valid output for both empty and
  populated crontab.extras inputs
- Instance template includes an example crontab.extras (no-clobber)
- CANASTA_ENABLE_CRON is a known config key

Kubernetes is unchanged — K8s backup scheduling continues to use
native CronJob resources. No K8s tests here.
"""

import os
import re

import jinja2
import pytest
import yaml


REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
DOCKERFILE_CRON = os.path.join(REPO_ROOT, "Dockerfile.cron")
DOCKER_WORKFLOW = os.path.join(
    REPO_ROOT, ".github", "workflows", "docker.yml",
)
COMPOSE = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "files", "compose", "docker-compose.yml",
)
CRON_TEMPLATE = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "templates", "crontab.j2",
)
REWRITE_CRON = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "rewrite_cron.yml",
)
SYNC_PROFILES = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "sync_compose_profiles.yml",
)
SIDE_EFFECTS = os.path.join(
    REPO_ROOT, "roles", "config", "tasks", "_side_effects.yml",
)
KNOWN_KEYS = os.path.join(
    REPO_ROOT, "roles", "config", "defaults", "main.yml",
)
INSTANCE_TEMPLATE_EXTRAS = os.path.join(
    REPO_ROOT, "instance_template", "config", "crontab.extras",
)


class TestDockerfile:
    def test_dockerfile_cron_exists(self):
        assert os.path.isfile(DOCKERFILE_CRON)

    def test_dockerfile_cron_pins_supercronic(self):
        with open(DOCKERFILE_CRON) as f:
            content = f.read()
        # Version is pinned
        assert re.search(r"SUPERCRONIC_VERSION=v\d+\.\d+\.\d+", content), (
            "Supercronic version must be pinned in Dockerfile.cron"
        )
        # Both amd64 and arm64 sha256s are pinned
        assert "SUPERCRONIC_AMD64_SHA256=" in content
        assert "SUPERCRONIC_ARM64_SHA256=" in content
        # sha256 is verified at install time
        assert "sha256sum -c -" in content, (
            "Dockerfile.cron must verify the downloaded supercronic binary"
        )

    def test_dockerfile_cron_installs_docker_cli(self):
        """The cron container needs docker-cli to invoke 'docker exec'
        or 'docker run' for scheduled jobs."""
        with open(DOCKERFILE_CRON) as f:
            content = f.read()
        assert "docker-cli" in content

    def test_dockerfile_cron_uses_inotify(self):
        """With -inotify, supercronic reloads the crontab automatically
        when the file mounted from the host changes (so canasta
        restart isn't required for every schedule edit)."""
        with open(DOCKERFILE_CRON) as f:
            content = f.read()
        assert "-inotify" in content


class TestDockerWorkflow:
    def test_workflow_builds_both_images(self):
        with open(DOCKER_WORKFLOW) as f:
            workflow = yaml.safe_load(f)
        build_job = workflow["jobs"]["build"]
        matrix = build_job["strategy"]["matrix"]["include"]
        images = {entry["image"] for entry in matrix}
        assert "canasta-ansible" in images
        assert "canasta-cron" in images

    def test_canasta_cron_build_uses_dockerfile_cron(self):
        with open(DOCKER_WORKFLOW) as f:
            workflow = yaml.safe_load(f)
        matrix = workflow["jobs"]["build"]["strategy"]["matrix"]["include"]
        cron_entry = next(e for e in matrix if e["image"] == "canasta-cron")
        assert cron_entry["dockerfile"] == "Dockerfile.cron"


class TestComposeService:
    def test_cron_service_exists_under_profile(self):
        with open(COMPOSE) as f:
            compose = yaml.safe_load(f)
        assert "cron" in compose["services"], (
            "docker-compose.yml must declare a 'cron' service"
        )
        svc = compose["services"]["cron"]
        assert "cron" in svc["profiles"], (
            "cron service must be gated on the 'cron' Compose profile"
        )

    def test_cron_service_mounts_docker_socket_readonly(self):
        with open(COMPOSE) as f:
            compose = yaml.safe_load(f)
        svc = compose["services"]["cron"]
        socket_mount = next(
            (v for v in svc["volumes"]
             if "/var/run/docker.sock" in v), None,
        )
        assert socket_mount is not None, (
            "cron service must mount the docker socket"
        )
        assert socket_mount.endswith(":ro"), (
            "docker socket must be mounted read-only on the cron service"
        )

    def test_cron_service_mounts_rendered_crontab(self):
        with open(COMPOSE) as f:
            compose = yaml.safe_load(f)
        svc = compose["services"]["cron"]
        crontab_mount = next(
            (v for v in svc["volumes"]
             if "/etc/supercronic/crontab" in v), None,
        )
        assert crontab_mount is not None, (
            "cron service must mount the rendered crontab into "
            "/etc/supercronic/crontab"
        )

    def test_cron_service_image_uses_canasta_cron(self):
        with open(COMPOSE) as f:
            compose = yaml.safe_load(f)
        svc = compose["services"]["cron"]
        assert "ghcr.io/canastawiki/canasta-cron" in svc["image"]


class TestSyncComposeProfiles:
    def test_cron_profile_toggled_by_canasta_enable_cron(self):
        with open(SYNC_PROFILES) as f:
            content = f.read()
        assert "CANASTA_ENABLE_CRON" in content
        assert "'cron'" in content


class TestRewriteCron:
    def test_rewrite_cron_task_exists(self):
        assert os.path.isfile(REWRITE_CRON)

    def test_rewrite_cron_reads_extras_and_renders_crontab(self):
        with open(REWRITE_CRON) as f:
            content = f.read()
        assert "config/crontab.extras" in content
        assert "crontab.j2" in content
        # Writes the rendered output to config/crontab (the path
        # mounted into the cron container).
        assert "dest: \"{{ instance_path }}/config/crontab\"" in content


class TestCrontabTemplate:
    """Render crontab.j2 against real inputs and verify output shape."""

    @staticmethod
    def _render(**ctx):
        with open(CRON_TEMPLATE) as f:
            src = f.read()
        env = jinja2.Environment(
            undefined=jinja2.StrictUndefined,
            keep_trailing_newline=True,
        )
        return env.from_string(src).render(**ctx)

    def test_empty_extras_produces_just_header(self):
        out = self._render(_crontab_extras_content="")
        assert "Auto-generated by Canasta-Ansible" in out
        assert "User-defined jobs" not in out

    def test_populated_extras_are_included_verbatim(self):
        extras = (
            "# SEBoK mwlib cache purge\n"
            "50 3 * * * docker exec sebok-mwlib-1 mw-serve-ctl "
            "--cache-dir=/var/cache/mwlib/ --purge-cache=2160\n"
        )
        out = self._render(_crontab_extras_content=extras)
        assert "User-defined jobs" in out
        assert "50 3 * * * docker exec sebok-mwlib-1" in out

    def test_missing_extras_variable_is_safe(self):
        """If rewrite_cron.yml hasn't set the fact (unreachable in
        practice but worth guarding), the template must still render."""
        with open(CRON_TEMPLATE) as f:
            src = f.read()
        # StrictUndefined would catch any non-defaulted reference.
        env = jinja2.Environment(
            undefined=jinja2.StrictUndefined,
            keep_trailing_newline=True,
        )
        env.from_string(src).render()  # no raise


class TestInstanceTemplate:
    def test_example_crontab_extras_exists(self):
        """The instance_template must ship an example crontab.extras
        so operators see the extension point on first create. Copied
        no-clobber, so existing customer files aren't overwritten."""
        assert os.path.isfile(INSTANCE_TEMPLATE_EXTRAS)

    def test_example_documents_syntax_and_docker_exec_pattern(self):
        with open(INSTANCE_TEMPLATE_EXTRAS) as f:
            content = f.read()
        assert "docker exec" in content
        assert "docker run" in content


class TestKnownKeys:
    def test_canasta_enable_cron_is_a_known_key(self):
        with open(KNOWN_KEYS) as f:
            defaults = yaml.safe_load(f)
        # canasta_known_keys is a list of dicts ({name, group,
        # description, default}) since #398; project names to check
        # membership.
        names = [e["name"] for e in defaults["canasta_known_keys"]]
        assert "CANASTA_ENABLE_CRON" in names, (
            "CANASTA_ENABLE_CRON must be in canasta_known_keys so "
            "'canasta config set CANASTA_ENABLE_CRON=true' works without --force"
        )


class TestSideEffects:
    def test_canasta_enable_cron_triggers_profile_sync(self):
        """Changing CANASTA_ENABLE_CRON via config set must trigger
        sync_compose_profiles so the cron service is added to or
        removed from COMPOSE_PROFILES on the next restart."""
        with open(SIDE_EFFECTS) as f:
            content = f.read()
        assert "'CANASTA_ENABLE_CRON'" in content
