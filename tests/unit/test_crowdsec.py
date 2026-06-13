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

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "..", "filter_plugins")
)
from canasta_crowdsec import (  # noqa: E402
    canasta_crowdsec_status_bouncers,
    canasta_crowdsec_blocklist_breakdown,
)


REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
COMPOSE_PATH = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "files", "compose", "docker-compose.yml",
)
CADDYFILE_J2 = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "templates", "Caddyfile.j2",
)

# Must match _CADDY_PLUGIN_IMAGE in direct_commands/_helpers.py and the
# literal in sync_compose_profiles.yml.
PLUGIN_CADDY_IMAGE = "ghcr.io/canastawiki/canasta-caddy:2.11.3"


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
        # Faster-than-default streaming poll for quicker ban propagation.
        assert "ticker_interval 15s" in out
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


class TestCrowdsecGitopsDurability:
    """On a gitops instance, .env is re-rendered from env.template +
    hosts/<host>/vars.yaml on every pull/regenerate. A feature key only
    survives that round trip if it is a placeholder key; otherwise the
    render resets it to the off state captured at init time, silently
    disabling CrowdSec on the next restart."""

    def _placeholder_keys(self):
        gitops_vars = yaml.safe_load(
            _read(os.path.join(REPO_ROOT, "roles", "gitops", "vars", "main.yml"))
        )
        return gitops_vars["gitops_placeholder_keys"]

    def test_crowdsec_inputs_are_placeholder_keys(self):
        keys = self._placeholder_keys()
        for k in (
            "CANASTA_ENABLE_CROWDSEC",
            "CROWDSEC_BOUNCER_API_KEY",
            "CADDY_TRUSTED_PROXIES",
        ):
            assert k in keys, (
                "%s must be a gitops placeholder key or it is dropped from "
                ".env on the next gitops render/pull" % k
            )

    def test_derived_keys_not_persisted(self):
        # CANASTA_CADDY_IMAGE and COMPOSE_PROFILES are re-derived from the
        # feature flags by sync_compose_profiles on start; persisting them
        # would freeze a value the start sequence already reconciles.
        keys = self._placeholder_keys()
        assert "CANASTA_CADDY_IMAGE" not in keys
        assert "COMPOSE_PROFILES" not in keys


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
        assert PLUGIN_CADDY_IMAGE in content

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

    def test_image_reconcile_matches_managed_repo_by_prefix(self):
        # An instance carrying an OLDER managed tag must still be recognized
        # as managed (and bumped), not stranded on the stale image.
        sync = _read(os.path.join(
            REPO_ROOT, "roles", "orchestrator", "tasks",
            "sync_compose_profiles.yml",
        ))
        assert "startswith(_managed_caddy_prefix)" in sync
        helpers = _read(os.path.join(
            REPO_ROOT, "direct_commands", "_helpers.py",
        ))
        assert "_managed_caddy_prefix" in helpers
        assert ".startswith(" in helpers


class TestCrowdsecConfigBackfill:
    """config/crowdsec/{acquis,whitelists}.yaml must reach an instance that
    enables CrowdSec after create. Otherwise Docker creates directories at
    the bind-mount paths, the engine gets a directory where its acquisition
    file should be, and detection silently never works."""

    def _ensure(self):
        return _read(os.path.join(
            REPO_ROOT, "roles", "orchestrator", "tasks",
            "ensure_crowdsec_config.yml",
        ))

    def test_template_ships_both_files(self):
        for f in ("acquis.yaml", "whitelists.yaml"):
            assert os.path.isfile(os.path.join(
                REPO_ROOT, "instance_template", "config", "crowdsec", f,
            )), "instance_template must ship %s for the wholesale copy" % f

    def test_backfill_copies_both_files_no_clobber(self):
        content = self._ensure()
        assert "force: false" in content, "backfill must not clobber edits"
        assert "acquis.yaml" in content
        assert "whitelists.yaml" in content

    def test_backfill_clears_stray_directory(self):
        # A prior crash-loop can leave Docker-created dirs at the file paths.
        content = self._ensure()
        assert "isdir" in content
        assert "state: absent" in content

    def test_wired_into_start_path_gated_on_profile(self):
        sync = _read(os.path.join(
            REPO_ROOT, "roles", "orchestrator", "tasks",
            "sync_compose_profiles.yml",
        ))
        assert "ensure_crowdsec_config.yml" in sync, (
            "backfill must run from sync_compose_profiles (the pre-up "
            "chokepoint hit by start/init/config-set)"
        )
        assert "'crowdsec' in _desired_profiles" in sync


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
            REPO_ROOT, "images", "caddy", "Dockerfile",
        )
        content = _read(path)
        assert "xcaddy build" in content
        assert "caddy-crowdsec-bouncer/http" in content

    def test_publish_workflow_targets_caddy_image(self):
        path = os.path.join(
            REPO_ROOT, ".github", "workflows", "docker-caddy.yml",
        )
        content = _read(path)
        assert "ghcr.io/canastawiki/canasta-caddy" in content


class TestCrowdsecCommands:
    def _defs(self):
        return canasta_cli.load_definitions()

    def test_subcommand_group_registered(self):
        assert canasta_cli.SUBCOMMAND_GROUPS.get("crowdsec") == [
            "bouncer-enroll", "console-enroll", "reload",
            "status", "scenarios", "alerts", "metrics",
            "ban", "unban",
        ], (
            "crowdsec subcommand group must expose bouncer-enroll/"
            "console-enroll/reload/status/scenarios/alerts/metrics/ban/unban"
        )

    def test_group_umbrella_defined(self):
        defs = self._defs()
        groups = {g["name"] for g in defs.get("command_groups", [])}
        assert "crowdsec" in groups, (
            "crowdsec needs an umbrella entry under command_groups so the "
            "group has help text"
        )

    @pytest.mark.parametrize("cmd_name,playbook", [
        ("crowdsec_bouncer_enroll", "crowdsec_bouncer_enroll.yml"),
        ("crowdsec_console_enroll", "crowdsec_console_enroll.yml"),
        ("crowdsec_reload", "crowdsec_reload.yml"),
        ("crowdsec_status", "crowdsec_status.yml"),
        ("crowdsec_scenarios", "crowdsec_scenarios.yml"),
        ("crowdsec_alerts", "crowdsec_alerts.yml"),
        ("crowdsec_metrics", "crowdsec_metrics.yml"),
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
            REPO_ROOT, "roles", "crowdsec", "tasks", "bouncer_enroll.yml",
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

    def test_key_store_uses_override_not_settings_extra_var(self):
        """Regression: auto-enroll runs inside a config-set-triggered
        restart, where `settings` is an Ansible extra-var that
        include_role vars cannot override. Passing the key as `settings:`
        silently re-applied the OUTER setting, never stored the key, and
        looped the restart/auto-enroll cycle forever. The nested call must
        use config_settings_override, which set.yml honors over `settings`.
        """
        content = self._enroll()
        assert "config_settings_override: \"CROWDSEC_BOUNCER_API_KEY=" in content, (
            "enroll must pass the key via config_settings_override so it "
            "survives the extra-var precedence of `settings`"
        )
        # set.yml must resolve the override ahead of the extra-var.
        set_yml = _read(os.path.join(
            REPO_ROOT, "roles", "config", "tasks", "set.yml",
        ))
        assert "config_settings_override | default(settings)" in set_yml, (
            "set.yml must read config_settings_override (falling back to the "
            "settings extra-var) so internal callers can substitute the key"
        )

    def test_key_handling_is_no_log(self):
        # The cscli-add step captures the raw key under no_log.
        content = self._enroll()
        assert "no_log: true" in content, (
            "the cscli-add task must be no_log so the captured key never "
            "lands in Ansible output"
        )

    def test_value_bearing_set_is_no_log_for_secrets(self):
        # no_log on the enclosing include_role does NOT propagate, so the
        # .env write that carries the key must suppress itself on a
        # secret-key match. enroll's old include-level no_log was a no-op.
        set_single = _read(os.path.join(
            REPO_ROOT, "roles", "config", "tasks", "_set_single.yml",
        ))
        assert "no_log:" in set_single
        assert "canasta_secret_key_pattern" in set_single
        defaults = yaml.safe_load(
            _read(os.path.join(REPO_ROOT, "roles", "config", "defaults", "main.yml"))
        )
        pattern = defaults["canasta_secret_key_pattern"]
        assert re.search(pattern, "CROWDSEC_BOUNCER_API_KEY"), (
            "secret pattern must match the bouncer key"
        )
        assert re.search(pattern, "MYSQL_PASSWORD")
        assert re.search(pattern, "CANASTA_ENABLE_CROWDSEC") is None, (
            "non-secret toggle must not be suppressed"
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


class TestCrowdsecInspectionRoles:
    """The read-only inspection commands (scenarios/alerts/metrics) that
    replace reaching for `docker compose exec crowdsec cscli ...`. Each must
    run the shared preflight (Compose-only, engine running) and be read-only
    (changed_when: false), never mutating engine state."""

    def _role(self, name):
        return _read(os.path.join(
            REPO_ROOT, "roles", "crowdsec", "tasks", name,
        ))

    def test_scenarios_lists_collections_and_scenarios(self):
        content = self._role("scenarios.yml")
        assert "_preflight.yml" in content
        assert "cscli collections list" in content
        assert "cscli scenarios list" in content, (
            "scenarios must list scenarios — the docker command the wiki "
            "tells users to run by hand"
        )
        assert "changed_when: false" in content, (
            "scenarios must be read-only"
        )

    def test_alerts_lists_alerts_with_optional_ip_filter(self):
        content = self._role("alerts.yml")
        assert "_preflight.yml" in content
        assert "'cscli', 'alerts', 'list'" in content
        # The --ip filter is appended only when provided (argv, not a shell
        # string), mirroring ban's optional-flag handling.
        assert "'--ip', ip | string" in content
        assert "changed_when: false" in content, "alerts must be read-only"

    def test_metrics_reads_engine_metrics_resiliently(self):
        content = self._role("metrics.yml")
        assert "_preflight.yml" in content
        assert "cscli metrics" in content
        assert "changed_when: false" in content, "metrics must be read-only"
        # Metrics depend on the (default-on) prometheus endpoint; a non-zero
        # rc must surface stderr, not raise an Ansible traceback.
        assert "failed_when: false" in content, (
            "metrics must not hard-fail when prometheus is disabled"
        )
        assert "stderr" in content


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


def _bouncer(name, last_pull=None, ip_address=None):
    """A minimal `cscli bouncers list -o json` entry."""
    return {
        "name": name, "last_pull": last_pull,
        "ip_address": ip_address, "valid": True,
    }


class TestCrowdsecStatusBouncersDisplay:
    """`crowdsec status` collapses the canasta-caddy bouncer family for
    display: CrowdSec auto-creates an undeletable 'canasta-caddy@<ip>' child
    each time the caddy container reconnects from a new IP, so the raw list
    accumulates harmless stale 'valid' rows (issue #619). The status filter
    shows the single live registration plus a count of the duplicates rather
    than listing each one."""

    def test_empty_and_none_render_none(self):
        assert canasta_crowdsec_status_bouncers([]) == "  (none)"
        assert canasta_crowdsec_status_bouncers(None) == "  (none)"

    def test_single_bouncer_no_stale_note(self):
        out = canasta_crowdsec_status_bouncers([
            _bouncer("canasta-caddy", "2026-06-11T12:00:00Z", "172.18.0.3"),
        ])
        assert "canasta-caddy — active" in out
        assert "172.18.0.3" in out
        assert "2026-06-11T12:00:00Z" in out
        assert "stale" not in out  # no duplicates -> no note

    def test_duplicates_collapse_to_live_plus_count(self):
        # The real aic shape: stale base + stale child + live child.
        out = canasta_crowdsec_status_bouncers([
            _bouncer("canasta-caddy", "2026-06-11T02:54:46Z", "172.18.0.4"),
            _bouncer("canasta-caddy@172.18.0.5", "2026-06-11T03:44:44Z", "172.18.0.5"),
            _bouncer("canasta-caddy@172.18.0.3", "2026-06-11T10:42:33Z", "172.18.0.3"),
        ])
        # The live (most-recently-pulled) registration is shown as active.
        assert "canasta-caddy@172.18.0.3 — active" in out
        assert "172.18.0.3" in out
        # The two stale duplicates are summarized, not listed individually.
        assert "2 stale auto-created duplicate" in out
        assert "enroll --force" in out
        assert "canasta-caddy@172.18.0.5" not in out
        # Exactly one active line (the note also contains the word "active").
        assert out.count("— active") == 1

    def test_live_base_with_stale_children(self):
        out = canasta_crowdsec_status_bouncers([
            _bouncer("canasta-caddy", "2026-06-11T12:00:00Z", "172.18.0.3"),
            _bouncer("canasta-caddy@172.18.0.9", "2026-06-11T09:00:00Z", "172.18.0.9"),
        ])
        assert "canasta-caddy — active" in out
        assert "1 stale auto-created duplicate" in out

    def test_never_pulled_renders_never(self):
        out = canasta_crowdsec_status_bouncers([
            _bouncer("canasta-caddy", None, None),
        ])
        assert "active" in out
        assert "never" in out
        assert "IP ?" in out

    def test_other_bouncers_listed_verbatim(self):
        out = canasta_crowdsec_status_bouncers([
            _bouncer("canasta-caddy", "2026-06-11T12:00:00Z", "172.18.0.3"),
            _bouncer("some-firewall-bouncer", "2026-06-11T11:00:00Z", "10.0.0.2"),
        ])
        assert "canasta-caddy — active" in out
        assert "some-firewall-bouncer" in out
        assert "10.0.0.2" in out
        # A non-family name (no '@') is not folded into the caddy family.
        out2 = canasta_crowdsec_status_bouncers([
            _bouncer("canasta-caddy", "2026-06-11T12:00:00Z", "172.18.0.3"),
            _bouncer("canasta-caddy-extra", "2026-06-11T11:00:00Z", "10.0.0.2"),
        ])
        assert "canasta-caddy-extra" in out2
        assert "stale" not in out2


class TestCrowdsecBlocklistBreakdown:
    HEADER = ("id,source,ip,reason,action,country,as,events_count,"
              "expiration,simulated,alert_id")

    def _raw(self, rows):
        return "\n".join([self.HEADER] + rows)

    def test_groups_and_counts_by_list(self):
        raw = self._raw([
            "1,lists,Ip:1.1.1.1,otx-webscanners,ban,,,0,1h,false,16",
            "2,lists,Ip:2.2.2.2,otx-webscanners,ban,,,0,1h,false,16",
            "3,lists,Ip:3.3.3.3,firehol_dyndns_ponmocup,ban,,,0,1h,false,15",
        ])
        out = canasta_crowdsec_blocklist_breakdown(raw)
        # leading newline + header, then one line per list (real newlines, not
        # literal backslash-n — the caller can't add newlines in Jinja).
        assert "\\n" not in out
        assert out.startswith(
            "\n  Console blocklists in effect (cached locally):\n")
        lines = [ln for ln in out.splitlines() if ln.strip()]
        # header, then one line per list sorted by name (firehol_... before
        # otx-...), then the "clear them" note.
        assert "Console blocklists in effect" in lines[0]
        assert ("firehol_dyndns_ponmocup" in lines[1]
                and lines[1].rstrip().endswith("1"))
        assert ("otx-webscanners" in lines[2]
                and lines[2].rstrip().endswith("2"))
        # the note points at the purge flags so status is self-explanatory
        assert "Clear them with:" in out
        assert "--purge-blocklist <name>" in out
        assert "--purge-blocklists" in out

    def test_empty_when_no_decisions(self):
        # cscli prints just the header (or nothing) when there are none.
        assert canasta_crowdsec_blocklist_breakdown(self._raw([])) == ""
        assert canasta_crowdsec_blocklist_breakdown("") == ""
        assert canasta_crowdsec_blocklist_breakdown(None) == ""

    def test_renders_with_real_newlines_through_jinja(self):
        """Regression for the literal-\\n bug: the console line concatenates
        'enrolled' with the filter output in Jinja, so the result must contain
        real newlines (not the backslash-n that a Jinja '\\n' literal yields)."""
        import jinja2
        raw = self._raw([
            "1,lists,Ip:1.1.1.1,otx-webscanners,ban,,,0,1h,false,16",
        ])
        block = canasta_crowdsec_blocklist_breakdown(raw)
        env = jinja2.Environment()
        # Mirror status.yml: {{ 'enrolled' ~ _crowdsec_blocklist_lines }}
        rendered = env.from_string(
            "{{ 'enrolled' ~ block }}"
        ).render(block=block)
        assert "\\n" not in rendered
        assert rendered.splitlines()[0] == "enrolled"
        assert ("  Console blocklists in effect (cached locally):"
                in rendered.splitlines()[1])


class TestCrowdsecStatusWiring:
    def test_status_lists_bouncers_as_json_and_uses_filter(self):
        content = _read(os.path.join(
            REPO_ROOT, "roles", "crowdsec", "tasks", "status.yml",
        ))
        assert "cscli bouncers list -o json" in content, (
            "status must read the bouncer list as JSON so the family can be "
            "collapsed"
        )
        assert "canasta_crowdsec_status_bouncers" in content, (
            "status must render bouncers through the de-duplicating filter"
        )

    def test_status_filter_registered_in_ansible_cfg(self):
        cfg = _read(os.path.join(REPO_ROOT, "ansible.cfg"))
        assert "filter_plugins = filter_plugins" in cfg, (
            "the filter_plugins path must be registered so the status filter "
            "loads at runtime (role-local plugins don't auto-discover under "
            "include_role)"
        )

    def test_status_reports_capi_and_console(self):
        """status must surface Central API registration (the community
        blocklist) and console enrollment, so an un-registered engine is no
        longer a silent failure."""
        content = _read(os.path.join(
            REPO_ROOT, "roles", "crowdsec", "tasks", "status.yml",
        ))
        assert "cscli capi status" in content, (
            "status must probe Central API registration (community blocklist)"
        )
        assert "cscli console status" in content, (
            "status must probe console enrollment state"
        )

    def test_status_counts_blocklist_decisions(self):
        """Community-blocklist decisions are hidden from `cscli decisions
        list`, so status must count them by origin — otherwise a loaded
        blocklist reads as 'no decisions'."""
        content = _read(os.path.join(
            REPO_ROOT, "roles", "crowdsec", "tasks", "status.yml",
        ))
        assert "--origin CAPI" in content, (
            "status must count community-blocklist (CAPI) decisions, which are "
            "excluded from the default decisions list"
        )
        assert "--origin lists" in content, (
            "status must also count console-subscribed blocklist (lists) "
            "decisions, which are likewise excluded from the default list"
        )

    def test_status_summarizes_console_without_raw_table(self):
        """The raw `cscli console status` table includes an unrelated
        `console_management` row that reads as a problem; status must show a
        one-line enrolled/not-enrolled summary instead of dumping it."""
        content = _read(os.path.join(
            REPO_ROOT, "roles", "crowdsec", "tasks", "status.yml",
        ))
        assert "_crowdsec_console_line" in content, (
            "status must derive a one-line console summary"
        )
        assert "Console: {{ _crowdsec_console_line }}" in content, (
            "status must display the one-line console summary, not dump the "
            "raw cscli console table (which shows the misleading "
            "console_management row)"
        )

    def test_console_enrolled_detection_is_glyph_free(self):
        """Enrolled-detection must read the stable `-o raw` CSV (option,enabled)
        rather than scraping the human table's ✅ glyph, which is fragile to
        cscli formatting/locale/color changes."""
        content = _read(os.path.join(
            REPO_ROOT, "roles", "crowdsec", "tasks", "status.yml",
        ))
        assert "cscli console status -o raw" in content, (
            "console status must be read as raw CSV, not the human table"
        )
        assert "✅" not in content, (
            "enrolled-detection must not depend on the ✅ glyph"
        )


class TestCrowdsecConsoleEnrollRole:
    def _console(self):
        return _read(os.path.join(
            REPO_ROOT, "roles", "crowdsec", "tasks", "console_enroll.yml",
        ))

    def test_runs_cscli_console_enroll(self):
        content = self._console()
        assert "'cscli', 'console', 'enroll'" in content, (
            "console-enroll must call `cscli console enroll`"
        )

    def test_requires_a_key(self):
        content = self._console()
        assert "key is not defined" in content, (
            "console-enroll must fail clearly when no enrollment key is given"
        )

    def test_key_handling_is_no_log(self):
        content = self._console()
        assert "no_log: true" in content, (
            "the enroll step must be no_log so the key never lands in output"
        )

    def test_restarts_engine_to_apply(self):
        content = self._console()
        assert "docker compose restart crowdsec" in content, (
            "the engine must restart so it picks up the console credentials"
        )


class TestCrowdsecReloadRole:
    def _reload(self):
        return _read(os.path.join(
            REPO_ROOT, "roles", "crowdsec", "tasks", "reload.yml",
        ))

    def test_restarts_only_the_engine(self):
        """reload must bounce just the crowdsec container, not the whole
        instance."""
        content = self._reload()
        assert "docker compose restart crowdsec" in content, (
            "reload must restart only the crowdsec engine container"
        )

    def test_preflight_gated(self):
        content = self._reload()
        assert "_preflight.yml" in content, (
            "reload must run the shared crowdsec preflight (Compose-only, "
            "engine running)"
        )


class TestCrowdsecAutoEnroll:
    def test_start_auto_enrolls_when_enabled_without_key(self):
        """Enabling CrowdSec should enroll the bouncer on the next start,
        with no separate manual step — gated on the key being absent so it
        is a no-op once enrolled."""
        content = _read(os.path.join(
            REPO_ROOT, "roles", "orchestrator", "tasks", "start.yml",
        ))
        assert "tasks_from: bouncer_enroll.yml" in content, (
            "start.yml must auto-enroll the bouncer via the crowdsec role"
        )
        assert "CANASTA_ENABLE_CROWDSEC" in content, (
            "auto-enroll must be gated on CrowdSec being enabled"
        )
        assert "CROWDSEC_BOUNCER_API_KEY" in content, (
            "auto-enroll must be gated on the bouncer key being absent so it "
            "is idempotent and does not recurse through the restart"
        )

    def test_enroll_waits_for_lapi_ready(self):
        """Auto-enroll runs right after start, when the engine may still be
        booting, so enroll must poll the LAPI before issuing cscli calls."""
        content = _read(os.path.join(
            REPO_ROOT, "roles", "crowdsec", "tasks", "bouncer_enroll.yml",
        ))
        assert "cscli lapi status" in content, (
            "enroll must wait for the LAPI to be ready before adding a bouncer"
        )


class TestCrowdsecCapiRegistration:
    def _capi(self):
        return _read(os.path.join(
            REPO_ROOT, "roles", "crowdsec", "tasks", "ensure_capi.yml",
        ))

    def test_registers_capi_to_enable_community_blocklist(self):
        """Without CAPI registration there is no community blocklist and
        console enrollment is impossible; ensure_capi must register it."""
        content = self._capi()
        assert "cscli capi register" in content, (
            "ensure_capi must register the engine with the Central API"
        )
        assert "online_api_credentials.yaml" in content, (
            "the registration must write the CAPI credentials file"
        )

    def test_registration_is_idempotent(self):
        """It must skip once registered, so it is a cheap no-op on every
        subsequent start (gated on `cscli capi status` failing)."""
        content = self._capi()
        assert "cscli capi status" in content, (
            "ensure_capi must check capi status to stay idempotent"
        )
        assert "_crowdsec_capi_status.rc != 0" in content, (
            "registration must be gated on capi status failing"
        )

    def test_credentials_are_no_log(self):
        content = self._capi()
        assert "no_log: true" in content, (
            "the register step must be no_log so CAPI credentials never leak"
        )

    def test_restarts_engine_to_load_credentials(self):
        content = self._capi()
        assert "docker compose restart crowdsec" in content, (
            "the engine must restart to pick up the new CAPI credentials"
        )

    def test_start_ensures_capi_when_enabled(self):
        """start.yml must register CAPI when CrowdSec is enabled, before the
        bouncer auto-enroll (console enrollment depends on CAPI)."""
        content = _read(os.path.join(
            REPO_ROOT, "roles", "orchestrator", "tasks", "start.yml",
        ))
        assert "tasks_from: ensure_capi.yml" in content, (
            "start.yml must ensure CAPI registration via the crowdsec role"
        )
        assert content.index("tasks_from: ensure_capi.yml") < content.index(
            "tasks_from: bouncer_enroll.yml"
        ), "CAPI registration must run before bouncer auto-enroll"
