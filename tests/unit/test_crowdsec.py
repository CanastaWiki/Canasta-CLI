"""Tests for the optional CrowdSec Compose profile.

Covers the Compose orchestrator wiring:
- docker-compose.yml service definition, profile, volumes, and the
  env-driven caddy image override
- Caddyfile.j2 rendering of the bouncer directives (global block +
  per-site directive), including the "key optional" guard that keeps
  Caddy booting when no bouncer key is set
- rewrite_caddy.yml reading the .env keys
- canasta_known_keys membership
- sync_compose_profiles.yml / _side_effects.yml profile wiring
- the bundled acquisition config and the xcaddy Dockerfile

Rendering tests evaluate the real Jinja template against different
inputs so a flipped condition or wrong directive name is caught —
something structural string-greps wouldn't detect.
"""

import os
import re
import sys

import jinja2
import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
import canasta as canasta_cli  # noqa: E402 (the canasta.py module)


REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
COMPOSE_PATH = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "files", "compose", "docker-compose.yml",
)
CADDYFILE_J2 = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "templates", "Caddyfile.j2",
)

# Must match _CADDY_CROWDSEC_IMAGE in direct_commands/_helpers.py and the
# literal in sync_compose_profiles.yml.
CROWDSEC_CADDY_IMAGE = "ghcr.io/canastawiki/canasta-caddy-crowdsec:2.10.2"


def _load_compose():
    with open(COMPOSE_PATH) as f:
        return yaml.safe_load(f)


def _read(path):
    with open(path) as f:
        return f.read()


def _ansible_jinja_env():
    """Jinja2 Environment with the minimal Ansible-compatible filters the
    Caddyfile template uses (ternary, regex_replace, bool)."""
    env = jinja2.Environment(
        undefined=jinja2.StrictUndefined,
        keep_trailing_newline=True,
    )
    env.filters["ternary"] = lambda cond, a, b: a if cond else b
    env.filters["regex_replace"] = lambda s, pat, repl="": re.sub(pat, repl, str(s))
    env.filters["bool"] = lambda v: str(v).lower() in ("true", "1", "yes", "y")
    return env


def _render_caddyfile(**ctx):
    base = dict(
        _site_address="example.com",
        _backend="web:80",
        _observable=False,
        _os_user="",
        _os_password_hash="",
        _staging_certs=False,
    )
    base.update(ctx)
    src = _read(CADDYFILE_J2)
    return _ansible_jinja_env().from_string(src).render(**base)


class TestCrowdsecComposeService:
    def test_crowdsec_service_exists_behind_profile(self):
        svc = _load_compose()["services"]["crowdsec"]
        assert svc.get("profiles") == ["crowdsec"], (
            "crowdsec service must be gated behind the 'crowdsec' profile "
            "so it never starts for deployments that don't opt in"
        )

    def test_crowdsec_image_is_pinned(self):
        svc = _load_compose()["services"]["crowdsec"]
        assert svc["image"].startswith("crowdsecurity/crowdsec:v"), (
            "crowdsec image must be pinned to a specific tag, not floating"
        )

    def test_crowdsec_installs_caddy_collection(self):
        svc = _load_compose()["services"]["crowdsec"]
        env = svc.get("environment", [])
        joined = "\n".join(env) if isinstance(env, list) else str(env)
        assert "crowdsecurity/caddy" in joined, (
            "crowdsec must install the caddy collection to parse Caddy logs"
        )

    def test_crowdsec_reads_caddy_logs_readonly(self):
        svc = _load_compose()["services"]["crowdsec"]
        vols = svc.get("volumes", [])
        assert any("caddy-logs:/var/log/caddy:ro" in v for v in vols), (
            "crowdsec must mount the shared caddy-logs volume read-only"
        )

    def test_crowdsec_mounts_acquisition_config(self):
        svc = _load_compose()["services"]["crowdsec"]
        vols = svc.get("volumes", [])
        assert any("acquis.yaml" in v for v in vols), (
            "crowdsec must mount config/crowdsec/acquis.yaml so it knows "
            "which log to read"
        )

    def test_crowdsec_volumes_declared(self):
        vols = _load_compose().get("volumes", {})
        assert "crowdsec-db" in vols
        assert "crowdsec-config" in vols

    def test_default_deployment_has_no_crowdsec_profile_active(self):
        """The crowdsec service is profiled, so a stock 'docker compose
        up' (no profile) never starts it. Guard that it still carries the
        profile gate (a regression that dropped the profile would turn it
        on for everyone)."""
        svc = _load_compose()["services"]["crowdsec"]
        assert "profiles" in svc and svc["profiles"], (
            "crowdsec must always be profile-gated"
        )


class TestCaddyImageOverride:
    def test_caddy_image_is_env_overridable(self):
        svc = _load_compose()["services"]["caddy"]
        assert svc["image"].startswith("${CANASTA_CADDY_IMAGE:-"), (
            "caddy image must be overridable via CANASTA_CADDY_IMAGE so the "
            "crowdsec bouncer variant can be swapped in when enabled"
        )

    def test_caddy_default_image_unchanged(self):
        """The default (no override) must remain the stock upstream Caddy
        image — existing deployments must see zero change."""
        svc = _load_compose()["services"]["caddy"]
        assert "docker.io/library/caddy:2.10.2-alpine}" in svc["image"]

    def test_caddy_receives_bouncer_key_env(self):
        svc = _load_compose()["services"]["caddy"]
        env = svc.get("environment", [])
        joined = "\n".join(env) if isinstance(env, list) else str(env)
        assert "CROWDSEC_BOUNCER_API_KEY=${CROWDSEC_BOUNCER_API_KEY:-}" in joined, (
            "caddy must receive CROWDSEC_BOUNCER_API_KEY (empty default) so "
            "the {env.CROWDSEC_BOUNCER_API_KEY} placeholder resolves"
        )


class TestCaddyfileRendering:
    def test_no_crowdsec_directive_when_inactive(self):
        out = _render_caddyfile(_crowdsec_active=False)
        assert "crowdsec {" not in out
        assert "order crowdsec first" not in out
        # No global options block at all when neither staging nor crowdsec.
        assert not re.search(r"(?m)^\{\s*$", out), (
            "an empty/standalone global block must not be emitted when no "
            "global directive is active"
        )

    def test_crowdsec_directives_when_active(self):
        out = _render_caddyfile(_crowdsec_active=True)
        # Global options block contents.
        assert "order crowdsec first" in out
        assert "crowdsec {" in out
        assert "api_url http://crowdsec:8080" in out
        # The key is referenced via env placeholder, never inlined.
        assert "api_key {env.CROWDSEC_BOUNCER_API_KEY}" in out
        # Global block comes before the imported global file / site block.
        assert out.index("order crowdsec first") < out.index(
            "import /etc/caddy/Caddyfile.global"
        )

    def test_crowdsec_per_site_directive_when_active(self):
        out = _render_caddyfile(_crowdsec_active=True)
        # Bare per-site directive, inside the site block, after the site
        # import.
        assert "\n    crowdsec\n" in out
        assert out.index("import /etc/caddy/Caddyfile.site") < out.index(
            "\n    crowdsec\n"
        )

    def test_inactive_renders_like_before(self):
        """With crowdsec inactive the site block must be exactly the
        plain reverse_proxy (the pre-feature behavior)."""
        out = _render_caddyfile(_crowdsec_active=False, _backend="web:80")
        assert "reverse_proxy web:80" in out

    def test_staging_and_crowdsec_share_one_global_block(self):
        """Caddy allows only one global options block. When both staging
        certs and crowdsec are active, both directives must appear inside
        a single block."""
        out = _render_caddyfile(_crowdsec_active=True, _staging_certs=True)
        assert "acme_ca" in out
        assert "order crowdsec first" in out
        # Exactly one global-options opener: a line that is just '{'.
        openers = re.findall(r"(?m)^\{\s*$", out)
        assert len(openers) == 1, (
            "staging + crowdsec must merge into a single global options "
            "block, got %d" % len(openers)
        )

    def test_key_optional_guard_documented_in_rewrite(self):
        """rewrite_caddy.yml must derive _crowdsec_active from BOTH the
        enable flag and a non-empty key — the guard that keeps Caddy
        booting when the key is unset (the original revert reason)."""
        content = _read(
            os.path.join(
                REPO_ROOT, "roles", "orchestrator", "tasks", "rewrite_caddy.yml",
            )
        )
        assert "_crowdsec_active" in content
        assert "CANASTA_ENABLE_CROWDSEC" in content
        assert "CROWDSEC_BOUNCER_API_KEY" in content


class TestCrowdsecConfigKeys:
    def _known_keys(self):
        defaults = yaml.safe_load(
            _read(os.path.join(REPO_ROOT, "roles", "config", "defaults", "main.yml"))
        )
        return {e["name"]: e for e in defaults["canasta_known_keys"]}

    def test_enable_flag_is_known_key_default_false(self):
        keys = self._known_keys()
        assert "CANASTA_ENABLE_CROWDSEC" in keys, (
            "CANASTA_ENABLE_CROWDSEC must be a known key so config set "
            "doesn't require --force"
        )
        assert keys["CANASTA_ENABLE_CROWDSEC"].get("default") == "false", (
            "CrowdSec must default OFF so existing deployments are unaffected"
        )

    def test_bouncer_key_is_known_key(self):
        keys = self._known_keys()
        assert "CROWDSEC_BOUNCER_API_KEY" in keys


class TestCrowdsecProfileSync:
    def test_sync_compose_profiles_handles_crowdsec(self):
        content = _read(
            os.path.join(
                REPO_ROOT, "roles", "orchestrator", "tasks",
                "sync_compose_profiles.yml",
            )
        )
        assert "'crowdsec'" in content
        assert "CANASTA_ENABLE_CROWDSEC" in content
        # Image reconciliation must be present and target the managed key.
        assert "CANASTA_CADDY_IMAGE" in content
        assert CROWDSEC_CADDY_IMAGE in content

    def test_side_effects_triggers_profile_sync_for_crowdsec(self):
        content = _read(
            os.path.join(
                REPO_ROOT, "roles", "config", "tasks", "_side_effects.yml",
            )
        )
        assert "'CANASTA_ENABLE_CROWDSEC'" in content, (
            "_side_effects.yml must include CANASTA_ENABLE_CROWDSEC in the "
            "compose profile-sync trigger list"
        )


class TestCrowdsecBundledFiles:
    def test_acquisition_config_present_and_valid(self):
        path = os.path.join(
            REPO_ROOT, "instance_template", "config", "crowdsec", "acquis.yaml",
        )
        assert os.path.exists(path), (
            "acquis.yaml must ship in the instance template so the crowdsec "
            "bind mount target always exists when the profile is enabled"
        )
        parsed = yaml.safe_load(_read(path))
        assert parsed["labels"]["type"] == "caddy", (
            "acquisition labels.type must be 'caddy' so the crowdsecurity/"
            "caddy parser picks up the log"
        )
        assert any(
            "/var/log/caddy" in f for f in parsed["filenames"]
        )

    def test_xcaddy_dockerfile_builds_bouncer_module(self):
        path = os.path.join(
            REPO_ROOT, "images", "caddy-crowdsec", "Dockerfile",
        )
        content = _read(path)
        assert "xcaddy build" in content
        assert "caddy-crowdsec-bouncer/http" in content

    def test_publish_workflow_targets_crowdsec_image(self):
        path = os.path.join(
            REPO_ROOT, ".github", "workflows", "docker-caddy-crowdsec.yml",
        )
        content = _read(path)
        assert "ghcr.io/canastawiki/canasta-caddy-crowdsec" in content


class TestCrowdsecCommands:
    def _defs(self):
        return canasta_cli.load_definitions()

    def test_subcommand_group_registered(self):
        assert canasta_cli.SUBCOMMAND_GROUPS.get("crowdsec") == [
            "enroll", "status", "ban", "unban",
        ], "crowdsec subcommand group must expose enroll/status/ban/unban"

    def test_group_umbrella_defined(self):
        defs = self._defs()
        groups = {g["name"] for g in defs.get("command_groups", [])}
        assert "crowdsec" in groups, (
            "crowdsec needs an umbrella entry under command_groups so the "
            "group has help text"
        )

    @pytest.mark.parametrize("cmd_name,playbook", [
        ("crowdsec_enroll", "crowdsec_enroll.yml"),
        ("crowdsec_status", "crowdsec_status.yml"),
        ("crowdsec_ban", "crowdsec_ban.yml"),
        ("crowdsec_unban", "crowdsec_unban.yml"),
    ])
    def test_command_defined_with_playbook(self, cmd_name, playbook):
        defs = self._defs()
        cmd = next(
            (c for c in defs["commands"] if c["name"] == cmd_name), None,
        )
        assert cmd is not None, "%s must be defined" % cmd_name
        assert cmd.get("playbook") == playbook
        assert os.path.exists(
            os.path.join(REPO_ROOT, "playbooks", playbook)
        ), "playbook %s must exist" % playbook

    def test_enable_disable_not_in_group(self):
        """Enable/disable stays as `canasta config set`, matching the
        other optional features — it must NOT be duplicated as crowdsec
        subcommands."""
        assert "enable" not in canasta_cli.SUBCOMMAND_GROUPS["crowdsec"]
        assert "disable" not in canasta_cli.SUBCOMMAND_GROUPS["crowdsec"]


class TestCrowdsecEnrollRole:
    def _enroll(self):
        return _read(os.path.join(
            REPO_ROOT, "roles", "crowdsec", "tasks", "enroll.yml",
        ))

    def test_registers_bouncer_and_captures_raw_key(self):
        content = self._enroll()
        assert "cscli bouncers add canasta-caddy -o raw" in content, (
            "enroll must register the bouncer and capture the raw key"
        )

    def test_force_revokes_existing_bouncer(self):
        content = self._enroll()
        assert "cscli bouncers delete canasta-caddy" in content, (
            "enroll must be able to revoke an existing bouncer (--force / "
            "orphaned registration)"
        )

    def test_persists_key_via_config_set(self):
        """Storing the key must go through config set so the Caddyfile
        re-renders, gitops vars update, and the instance restarts."""
        content = self._enroll()
        assert "CROWDSEC_BOUNCER_API_KEY=" in content
        assert "tasks_from: set.yml" in content

    def test_key_handling_is_no_log(self):
        content = self._enroll()
        assert "no_log: true" in content, (
            "the token-bearing tasks must be no_log so the key never lands "
            "in Ansible output"
        )

    def test_compose_only_guard(self):
        # Guard lives in the shared preflight include.
        preflight = _read(os.path.join(
            REPO_ROOT, "roles", "crowdsec", "tasks", "_preflight.yml",
        ))
        assert "not in ['compose']" in preflight
        assert "Kubernetes" in preflight


class TestCrowdsecStatusRole:
    def test_lists_bouncers_and_decisions(self):
        content = _read(os.path.join(
            REPO_ROOT, "roles", "crowdsec", "tasks", "status.yml",
        ))
        assert "cscli bouncers list" in content
        assert "cscli decisions list" in content


class TestCrowdsecBanUnban:
    def test_ban_requires_positional_ip(self):
        defs = canasta_cli.load_definitions()
        cmd = next(c for c in defs["commands"] if c["name"] == "crowdsec_ban")
        ip = next((p for p in cmd["parameters"] if p["name"] == "ip"), None)
        assert ip is not None
        assert ip.get("positional") is True
        assert ip.get("required") is True

    def test_ban_role_adds_decision_with_optional_flags(self):
        content = _read(os.path.join(
            REPO_ROOT, "roles", "crowdsec", "tasks", "ban.yml",
        ))
        # Uses cscli decisions add against --ip, with duration/reason
        # appended only when provided (argv, not a shell string).
        assert "'cscli', 'decisions', 'add', '--ip'" in content
        assert "duration" in content
        assert "reason" in content

    def test_unban_role_deletes_decision(self):
        content = _read(os.path.join(
            REPO_ROOT, "roles", "crowdsec", "tasks", "unban.yml",
        ))
        assert "decisions" in content
        assert "delete" in content
        assert "--ip" in content


class TestCrowdsecWhitelist:
    def test_whitelist_ships_and_is_valid(self):
        path = os.path.join(
            REPO_ROOT, "instance_template", "config", "crowdsec",
            "whitelists.yaml",
        )
        assert os.path.exists(path), (
            "whitelists.yaml must ship in the instance template, like "
            "Caddyfile.global, ready to edit"
        )
        parsed = yaml.safe_load(_read(path))
        # A parser whitelist needs a 'whitelist' node to load cleanly.
        assert "whitelist" in parsed
        assert "reason" in parsed["whitelist"]

    def test_whitelist_mounted_into_parser_dir(self):
        svc = _load_compose()["services"]["crowdsec"]
        vols = svc.get("volumes", [])
        assert any(
            "whitelists.yaml" in v and "parsers/s02-enrich" in v for v in vols
        ), (
            "whitelists.yaml must mount into /etc/crowdsec/parsers/"
            "s02-enrich/ so CrowdSec loads it"
        )
