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
from canasta_caddy import meld_caddy_global_blocks  # noqa: E402


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
        # Caddyfile.global is now inlined + melded (not imported); the CLI
        # global block must still precede the site block. Caddyfile.site
        # stays a live import.
        assert "import /etc/caddy/Caddyfile.global" not in out
        assert out.index("order crowdsec first") < out.index(
            "import /etc/caddy/Caddyfile.site"
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

    def test_user_global_block_inlined_and_melded(self):
        """A global options block in Caddyfile.global is inlined and melded
        with the CLI global block — Caddy permits exactly one (#693)."""
        user_global = "{\n    email admin@example.com\n}\n"
        rendered = _render_caddyfile(
            _crowdsec_active=True, _caddyfile_global_content=user_global
        )
        out = meld_caddy_global_blocks(rendered)
        # Exactly one top-level global options opener after melding.
        assert len(re.findall(r"(?m)^\{\s*$", out)) == 1
        # Both CLI and user directives live inside it.
        assert "order crowdsec first" in out
        assert "email admin@example.com" in out
        # Site block and its live import are preserved.
        assert "import /etc/caddy/Caddyfile.site" in out
        assert "example.com {" in out

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

    def test_k8s_trusted_proxy_cidrs_is_known_key(self):
        # The K8s cluster-CIDR override must be settable without --force.
        keys = self._known_keys()
        assert "CADDY_TRUSTED_PROXY_CIDRS" in keys


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

    def test_xcaddy_dockerfile_builds_combine_ranges_module(self):
        # K8s real-client-IP behind a CDN combines the cluster CIDR with the
        # CDN ranges via caddy-combine-ip-ranges (issue #604).
        content = _read(os.path.join(
            REPO_ROOT, "images", "caddy", "Dockerfile",
        ))
        assert "caddy-combine-ip-ranges" in content
        assert "caddy-cdn-ranges" in content

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

    def test_preflight_supports_both_orchestrators(self):
        # CrowdSec now runs on K8s too (issue #604). The shared preflight must
        # no longer hard-refuse non-Compose, and must branch its engine-running
        # check: `docker compose ps` for Compose, a kubectl readiness probe of
        # the crowdsec sidecar for K8s.
        preflight = _read(os.path.join(
            REPO_ROOT, "roles", "crowdsec", "tasks", "_preflight.yml",
        ))
        assert "not in ['compose']" not in preflight, (
            "the Compose-only refusal must be gone — K8s is supported"
        )
        assert "docker compose ps" in preflight
        assert "kubectl get pods" in preflight
        assert "crowdsec=true" in preflight


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
    run the shared preflight (resolve, engine running) and be read-only
    (changed_when: false), never mutating engine state."""

    def _role(self, name):
        return _read(os.path.join(
            REPO_ROOT, "roles", "crowdsec", "tasks", name,
        ))

    @pytest.mark.parametrize("name", ["scenarios.yml", "alerts.yml", "metrics.yml"])
    def test_inspection_command_is_orchestrator_agnostic(self, name):
        # These run on K8s too (issue #604), so they must route cscli through
        # the resolved prefix — never the hardcoded Compose invocation, which
        # lives only in _resolve_cscli.yml. Without this, the rewritten
        # preflight (which no longer refuses K8s) would let them run
        # `docker compose exec` on a Kubernetes instance and fail confusingly.
        content = self._role(name)
        assert "docker compose exec" not in content, (
            f"{name} must use _cscli_prefix/_cscli_argv, not a hardcoded "
            "'docker compose exec'"
        )
        assert "_cscli_prefix" in content or "_cscli_argv" in content
        assert "_cscli_chdir" in content

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
        assert "_restart_engine.yml" in content, (
            "the engine must restart (via the shared, orchestrator-aware "
            "_restart_engine include) so it picks up the console credentials"
        )


class TestCrowdsecReloadRole:
    def _reload(self):
        return _read(os.path.join(
            REPO_ROOT, "roles", "crowdsec", "tasks", "reload.yml",
        ))

    def test_restarts_only_the_engine(self):
        """reload must bounce just the engine (the crowdsec container on
        Compose; the caddy pod that hosts the sidecar on K8s), via the shared
        orchestrator-aware restart include — not the whole instance."""
        content = self._reload()
        assert "_restart_engine.yml" in content, (
            "reload must restart the engine via the shared _restart_engine "
            "include"
        )

    def test_preflight_gated(self):
        content = self._reload()
        assert "_preflight.yml" in content, (
            "reload must run the shared crowdsec preflight (engine running)"
        )

    def test_purge_routes_through_resolved_prefix(self):
        # The console-blocklist purge runs cscli too, so on K8s it must use the
        # resolved prefix, not a hardcoded `docker compose exec` (issue #604) —
        # the rewritten preflight no longer refuses K8s, so a hardcoded purge
        # would otherwise run the Compose command on a cluster.
        content = self._reload()
        assert "docker compose exec" not in content, (
            "reload's purge must use _cscli_argv, not a hardcoded "
            "'docker compose exec'"
        )
        assert "_cscli_argv" in content
        assert "'cscli', 'decisions', 'delete'" in content

    def test_purge_rebuilds_prefix_after_restart(self):
        # The restart rolls the engine. On K8s (Recreate strategy) that gives
        # the caddy pod a new name, so the cscli prefix built in preflight then
        # points at a dead pod. The purge must re-resolve the prefix AFTER
        # _restart_engine and BEFORE exec-ing cscli, or the delete targets a
        # pod that no longer exists (issue #604).
        content = self._reload()
        restart = content.index("_restart_engine.yml")
        resolve = content.index("_resolve_cscli.yml")
        delete = content.index("'cscli', 'decisions', 'delete'")
        assert restart < resolve < delete, (
            "reload must re-include _resolve_cscli.yml after the engine "
            "restart and before the purge delete, so the K8s prefix targets "
            "the post-rollout pod"
        )


class TestCrowdsecReportFormatting:
    """Folded (>-) display messages must collapse to a single logical line.
    A continuation line indented deeper than the first makes YAML preserve
    the line break instead of folding it to a space, which prints as a
    spurious blank line in the command output (regression: ban report)."""

    def _strings(self, name):
        tasks = yaml.safe_load(_read(os.path.join(
            REPO_ROOT, "roles", "crowdsec", "tasks", name)))
        found = []

        def walk(n):
            if isinstance(n, dict):
                for v in n.values():
                    walk(v)
            elif isinstance(n, list):
                for v in n:
                    walk(v)
            elif isinstance(n, str):
                found.append(n)
        walk(tasks)
        return found

    def _stray_newlines(self, s):
        # Newlines that survive YAML folding OUTSIDE a {{ }} expression are the
        # ones that reach the rendered output as blank lines.
        return re.sub(r"\{\{.*?\}\}", "", s, flags=re.S).count("\n")

    def test_ban_report_renders_on_one_line(self):
        msg = next(s for s in self._strings("ban.yml")
                   if s.startswith("Blocked {{ ip }}"))
        assert self._stray_newlines(msg) == 0, (
            "ban report must fold to one line — no stray newline before "
            "'on instance'"
        )
        # Render both branches to prove the visible output is single-line.
        for dur in ("10m", ""):
            out = jinja2.Environment().from_string(msg).render(
                ip="203.0.113.5", duration=dur, instance_id="mysite")
            assert "\n" not in out, f"ban report wrapped (duration={dur!r}): {out!r}"

    def test_reload_report_and_purge_line_render_on_one_line(self):
        strings = self._strings("reload.yml")
        report = next(s for s in strings
                      if s.startswith("Reloaded the CrowdSec engine"))
        purge = next(s for s in strings if s.startswith("Purged {{"))
        assert self._stray_newlines(report) == 0, "reload report must fold to one line"
        assert self._stray_newlines(purge) == 0, "purge note must fold to one line"


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
        assert "_restart_engine.yml" in content, (
            "the engine must restart (via the shared _restart_engine include) "
            "to pick up the new CAPI credentials"
        )

    def test_start_ensures_capi_when_enabled(self):
        """The shared autoenroll task (used by start + create) must register
        CAPI when CrowdSec is enabled, before the bouncer auto-enroll (console
        enrollment depends on CAPI)."""
        start = _read(os.path.join(
            REPO_ROOT, "roles", "orchestrator", "tasks", "start.yml",
        ))
        assert "crowdsec_autoenroll.yml" in start, (
            "start.yml must run the shared CrowdSec autoenroll task"
        )
        content = _read(os.path.join(
            REPO_ROOT, "roles", "orchestrator", "tasks",
            "crowdsec_autoenroll.yml",
        ))
        assert "tasks_from: ensure_capi.yml" in content, (
            "autoenroll must ensure CAPI registration via the crowdsec role"
        )
        assert content.index("tasks_from: ensure_capi.yml") < content.index(
            "tasks_from: bouncer_enroll.yml"
        ), "CAPI registration must run before bouncer auto-enroll"


# ---------------------------------------------------------------------------
# Kubernetes orchestrator support (issue #604)
# ---------------------------------------------------------------------------

HELM_DIR = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "files", "helm", "canasta",
)


def _read_helm(name):
    return _read(os.path.join(HELM_DIR, "templates", name))


class TestCrowdsecRestartEngine:
    """The shared, orchestrator-aware engine restart."""

    def _content(self):
        return _read(os.path.join(
            REPO_ROOT, "roles", "crowdsec", "tasks", "_restart_engine.yml",
        ))

    def test_compose_restarts_only_the_service(self):
        content = self._content()
        assert "docker compose restart crowdsec" in content

    def test_k8s_rolls_the_caddy_deployment(self):
        # The engine is a sidecar in the caddy pod, so the K8s equivalent is a
        # rollout of the caddy deployment, waited on so callers can exec into
        # the fresh pod.
        content = self._content()
        assert "kubectl rollout restart deployment/canasta-{{ instance_id }}-caddy" in content
        assert "kubectl rollout status deployment/canasta-{{ instance_id }}-caddy" in content


class TestCrowdsecResolveCscli:
    """The cscli command-prefix resolver used by every crowdsec subcommand."""

    def _content(self):
        return _read(os.path.join(
            REPO_ROOT, "roles", "crowdsec", "tasks", "_resolve_cscli.yml",
        ))

    def test_compose_prefix(self):
        content = self._content()
        assert "docker compose exec -T crowdsec" in content

    def test_k8s_prefix_targets_the_sidecar_container(self):
        # kubectl exec into the caddy pod, selecting the crowdsec sidecar.
        content = self._content()
        assert "kubectl exec" in content
        assert "-c crowdsec --" in content

    def test_preflight_builds_the_prefix(self):
        preflight = _read(os.path.join(
            REPO_ROOT, "roles", "crowdsec", "tasks", "_preflight.yml",
        ))
        assert "_resolve_cscli.yml" in preflight


class TestCrowdsecPreflightK8sReadiness:
    """The K8s caddy-pod readiness probe in _preflight.yml uses a jsonpath
    that contains a space (the 'name=ready ' separator). It must be quoted so
    Ansible's command parser keeps it as one argument — otherwise the trailing
    ' {end}' splits into its own token and kubectl reads it as a pod name,
    failing with 'name cannot be provided when a selector is specified'."""

    def _readiness_cmd(self):
        tasks = yaml.safe_load(_read(os.path.join(
            REPO_ROOT, "roles", "crowdsec", "tasks", "_preflight.yml")))
        found = []

        def walk(n):
            if isinstance(n, dict):
                cmd = n.get("ansible.builtin.command")
                if isinstance(cmd, dict) and "containerStatuses" in str(cmd.get("cmd", "")):
                    found.append(cmd["cmd"])
                for v in n.values():
                    walk(v)
            elif isinstance(n, list):
                for v in n:
                    walk(v)
        walk(tasks)
        assert found, "expected the jsonpath readiness command in _preflight.yml"
        return found[0]

    def test_readiness_jsonpath_is_a_single_argument(self):
        import shlex
        tokens = shlex.split(self._readiness_cmd().replace("{{ instance_id }}", "big"))
        assert "{end}" not in tokens, (
            "jsonpath must be quoted — an unquoted ' {end}' splits into its own "
            "token and kubectl reads it as a pod name"
        )
        jp = [t for t in tokens if t.startswith("jsonpath=")]
        assert len(jp) == 1, f"expected exactly one jsonpath token, got {jp}"
        assert jp[0].endswith("{end}"), "the full jsonpath (incl. {end}) must be one token"


class TestCrowdsecK8sCaddyImageReconcile:
    """The K8s caddy image + crowdsec.enabled are reconciled into values.yaml
    from .env at deploy time by k8s_reconcile_caddy.yml — the analog of
    Compose's start-time sync_compose_profiles. A single reconcile point serves
    every entry path (config set restart, create -e, hand edit), so the deployed
    manifests always match the feature flags. Because it runs at deploy time
    (.env already written), it reads .env directly with no overlay."""

    def _reconcile(self):
        return _read(os.path.join(
            REPO_ROOT, "roles", "orchestrator", "tasks",
            "k8s_reconcile_caddy.yml"))

    def test_reconcile_reads_flags_and_picks_plugin_image(self):
        content = self._reconcile()
        assert "CANASTA_ENABLE_CROWDSEC" in content
        assert "CADDY_TRUSTED_PROXIES" in content
        assert "cloudflare" in content and "imperva" in content
        assert PLUGIN_CADDY_IMAGE in content
        assert "caddy:2.10.2-alpine" in content

    def test_reconcile_sets_crowdsec_enabled_from_flag(self):
        content = self._reconcile()
        assert "'crowdsec': {'enabled':" in content, (
            "reconcile must set crowdsec.enabled in values.yaml from the flag"
        )

    def test_reconcile_only_touches_managed_images(self):
        # A custom operator image must be left untouched; managed = empty,
        # stock default, or any managed-prefix tag.
        content = self._reconcile()
        assert "ghcr.io/canastawiki/canasta-caddy:" in content
        assert ".startswith(" in content

    def test_reconcile_wired_into_start_and_create(self):
        start = _read(os.path.join(
            REPO_ROOT, "roles", "orchestrator", "tasks", "start.yml"))
        create = _read(os.path.join(
            REPO_ROOT, "roles", "create", "tasks", "_start.yml"))
        assert "k8s_reconcile_caddy.yml" in start, (
            "the start path must reconcile before reading values.yaml"
        )
        assert "k8s_reconcile_caddy.yml" in create, (
            "create's K8s deploy bypasses start.yml, so it must reconcile too"
        )

    def test_side_effects_no_longer_reconciles_k8s_caddy(self):
        # Superseded by the start-path reconcile; leaving a second copy here
        # would re-introduce the create/config-set divergence #685 closed.
        content = _read(os.path.join(
            REPO_ROOT, "roles", "config", "tasks", "_side_effects.yml"))
        assert "Reconcile K8s caddy plugin image" not in content
        assert "_config_value if _config_key == 'CANASTA_ENABLE_CROWDSEC'" not in content


class TestCrowdsecK8sChart:
    def test_values_has_crowdsec_block(self):
        values = yaml.safe_load(_read(os.path.join(HELM_DIR, "values.yaml")))
        assert values["crowdsec"]["enabled"] is False, (
            "crowdsec must default off so existing deployments are unaffected"
        )
        assert "crowdsec" in values["configData"]

    def test_caddy_deployment_gates_sidecar_on_enabled(self):
        content = _read_helm("deployment-caddy.yaml")
        # Everything crowdsec is behind the enabled gate.
        assert "{{- if .Values.crowdsec.enabled }}" in content
        # Sidecar container, log sharing, seed init container, key env.
        assert "name: crowdsec" in content
        assert "/var/log/caddy" in content
        assert "seed-crowdsec-config" in content
        assert "CROWDSEC_BOUNCER_API_KEY" in content
        assert "secretKeyRef" in content
        assert "optional: true" in content
        # RWO PVC sidecar forces a Recreate strategy.
        assert "type: Recreate" in content

    def test_crowdsec_service_routes_lapi_to_caddy_pod(self):
        content = _read_helm("service-aliases.yaml")
        assert "name: crowdsec" in content
        assert "port: 8080" in content
        # The Service selects the caddy pod (the sidecar lives there).
        crowdsec_block = content[content.index("name: crowdsec"):]
        assert "app.kubernetes.io/component: caddy" in crowdsec_block

    def test_pvc_and_configmap_exist_and_are_gated(self):
        pvc = _read_helm("pvc-crowdsec.yaml")
        cm = _read_helm("configmap-crowdsec.yaml")
        assert "{{- if .Values.crowdsec.enabled }}" in pvc
        assert "{{- if .Values.crowdsec.enabled }}" in cm
        assert "/var/lib/crowdsec/data" in pvc or "subPath" in pvc.lower()


class TestCrowdsecK8sConfigSync:
    def _content(self):
        return _read(os.path.join(
            REPO_ROOT, "roles", "orchestrator", "tasks", "k8s_sync_config.yml",
        ))

    def test_creates_bouncer_key_secret(self):
        content = self._content()
        assert "canasta-{{ instance_id }}-crowdsec" in content
        assert "CROWDSEC_BOUNCER_API_KEY" in content
        # The key Secret must be no_log so it never lands in output.
        secret_block = content[content.index("crowdsec bouncer key data"):]
        assert "no_log: true" in secret_block

    def test_syncs_acquisition_and_whitelist_into_configdata(self):
        content = self._content()
        assert "_crowdsec_config_data" in content
        assert "acquis.yaml" in content
        assert "whitelists.yaml" in content
        assert "'crowdsec': _crowdsec_config_data" in content


class TestCrowdsecK8sValuesPropagation:
    """crowdsec.enabled + the caddy plugin image reach the cluster via the
    deploy-time reconcile (k8s_reconcile_caddy.yml), not a config-set-time
    write in _side_effects (that path is superseded)."""

    def _content(self):
        return _read(os.path.join(
            REPO_ROOT, "roles", "orchestrator", "tasks",
            "k8s_reconcile_caddy.yml",
        ))

    def test_enable_flag_maps_to_chart_toggle(self):
        content = self._content()
        assert "'crowdsec': {'enabled':" in content

    def test_reconciles_caddy_plugin_image_on_k8s(self):
        content = self._content()
        # CrowdSec or a provider trusted-proxy mode swaps caddy.image to the
        # plugin image on K8s.
        assert PLUGIN_CADDY_IMAGE in content
        assert "CADDY_TRUSTED_PROXIES" in content


class TestCrowdsecAutoEnroll:
    """Create-time `-e CANASTA_ENABLE_CROWDSEC=true` must produce a working
    CrowdSec on BOTH orchestrators, not an inert engine. One shared,
    orchestrator-agnostic enroll task (crowdsec_autoenroll.yml) registers CAPI
    and enrolls the bouncer when enabled-but-unenrolled; the crowdsec role
    dispatches Compose vs k8s internally, so only the K8s sidecar-wait is
    guarded in the task."""

    def _autoenroll(self):
        return _read(os.path.join(
            REPO_ROOT, "roles", "orchestrator", "tasks",
            "crowdsec_autoenroll.yml"))

    def test_autoenroll_registers_capi_and_enrolls_bouncer(self):
        content = self._autoenroll()
        assert "ensure_capi.yml" in content
        assert "bouncer_enroll.yml" in content
        # Self-gates on CrowdSec being enabled.
        assert "CANASTA_ENABLE_CROWDSEC" in content

    def test_autoenroll_calls_bouncer_unconditionally(self):
        # bouncer_enroll must NOT be gated on a stored key: on K8s, disabling
        # CrowdSec prunes the engine PVC (wipes the bouncer registration) while
        # the key persists in .env, so re-enabling needs a re-enroll even with
        # a key present. bouncer_enroll itself enrolls when the engine has no
        # bouncer (idempotent), and bouncer_enroll_auto keeps it quiet.
        auto = self._autoenroll()
        assert "CROWDSEC_BOUNCER_API_KEY" not in auto, (
            "the bouncer enroll must not be gated on a stored key"
        )
        assert "bouncer_enroll_auto: true" in auto
        enroll = _read(os.path.join(
            REPO_ROOT, "roles", "crowdsec", "tasks", "bouncer_enroll.yml"))
        # bouncer_enroll re-enrolls when the engine has no bouncer registered.
        assert "not (_crowdsec_existing | bool)" in enroll

    def test_bouncer_enroll_auto_suppresses_already_enrolled_hint(self):
        # In the auto path a steady-state start would otherwise print the
        # interactive "already enrolled — use --force" hint on every start.
        enroll = _read(os.path.join(
            REPO_ROOT, "roles", "crowdsec", "tasks", "bouncer_enroll.yml"))
        hint = enroll[enroll.index("already enrolled"):]
        assert "not (bouncer_enroll_auto | default(false) | bool)" in hint

    def test_autoenroll_sidecar_wait_is_k8s_guarded(self):
        # The sidecar rollout wait is K8s-only; Compose starts the service
        # synchronously. The guard lives inside the orchestrator task (allowed
        # by the orchestrator-dispatch design), not in action code.
        content = self._autoenroll()
        assert "rollout status deployment/canasta-{{ instance_id }}-caddy" in content
        wait = content[content.index("rollout status"):]
        assert "instance_orchestrator | default('compose') in ['kubernetes', 'k8s']" in wait

    def test_autoenroll_shared_by_start_and_create(self):
        start = _read(os.path.join(
            REPO_ROOT, "roles", "orchestrator", "tasks", "start.yml"))
        create = _read(os.path.join(
            REPO_ROOT, "roles", "create", "tasks", "main.yml"))
        assert "crowdsec_autoenroll.yml" in start
        assert "crowdsec_autoenroll.yml" in create
        assert "k8s_crowdsec_autoenroll.yml" not in start
        assert "k8s_crowdsec_autoenroll.yml" not in create

    def test_create_enroll_is_orchestrator_agnostic(self):
        # The create-time enroll must NOT switch on the orchestrator in action
        # code (#696) — it dispatches unconditionally; the task self-gates.
        create = _read(os.path.join(
            REPO_ROOT, "roles", "create", "tasks", "main.yml"))
        start = create.index("Enroll CrowdSec bouncer")
        step = create[start:create.index("Step 14", start)]
        assert "crowdsec_autoenroll.yml" in step
        assert "when:" not in step, (
            "create-time enroll must dispatch unconditionally, not gate on the "
            "orchestrator"
        )

    def test_create_enrolls_after_register(self):
        # The crowdsec preflight resolves the instance from the registry, so
        # the create-time enroll must run after _register, not in _start.
        create = _read(os.path.join(
            REPO_ROOT, "roles", "create", "tasks", "main.yml"))
        assert create.index("_register.yml") < create.index(
            "crowdsec_autoenroll.yml"), (
            "create must enroll CrowdSec after registering the instance"
        )
        start = _read(os.path.join(
            REPO_ROOT, "roles", "create", "tasks", "_start.yml"))
        assert "crowdsec_autoenroll.yml" not in start, (
            "enroll must not run in _start (instance not yet registered)"
        )

    def test_create_first_start_defers_compose_enroll(self):
        # Compose's first start goes through orchestrator start.yml, which would
        # enroll before _register and fail the registry resolve. create defers
        # it with crowdsec_autoenroll: false; start.yml honors that flag.
        cstart = _read(os.path.join(
            REPO_ROOT, "roles", "create", "tasks", "_start.yml"))
        assert "crowdsec_autoenroll: false" in cstart
        start = _read(os.path.join(
            REPO_ROOT, "roles", "orchestrator", "tasks", "start.yml"))
        assert "crowdsec_autoenroll | default(true)" in start

    def test_autoenroll_is_self_contained(self):
        # The task derives its namespace from instance_id (a global fact in
        # both paths), so it must not depend on a caller-set _k8s_namespace.
        content = self._autoenroll()
        assert "-n canasta-{{ instance_id }}" in content
        assert "_k8s_namespace" not in content


class TestCrowdsecK8sTrustedProxy:
    def test_rewrite_caddy_defaults_cluster_cidrs(self):
        content = _read(os.path.join(
            REPO_ROOT, "roles", "orchestrator", "tasks", "rewrite_caddy.yml",
        ))
        assert "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16" in content
        assert "CADDY_TRUSTED_PROXY_CIDRS" in content
        assert "_tp_combine" in content

    def test_k8s_no_cdn_renders_static_cluster_cidrs(self):
        out = _render_caddyfile(
            _crowdsec_active=True,
            _trusted_proxies_enabled=True,
            _tp_combine=False,
            _tp_dynamic=False,
            _tp_mode="",
            _trusted_proxies_headers="X-Forwarded-For",
            _trusted_proxies_strict=True,
            _trusted_proxies_cidrs=["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"],
        )
        assert "trusted_proxies static 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16" in out
        assert "client_ip_headers X-Forwarded-For" in out
        assert "combine" not in out

    def test_k8s_cdn_renders_combine_of_static_and_cdn_ranges(self):
        out = _render_caddyfile(
            _crowdsec_active=True,
            _trusted_proxies_enabled=True,
            _tp_combine=True,
            _tp_dynamic=True,
            _tp_mode="cloudflare",
            _trusted_proxies_headers="Cf-Connecting-Ip",
            _trusted_proxies_strict=False,
            _trusted_proxies_cidrs=["10.0.0.0/8"],
        )
        assert "trusted_proxies combine {" in out
        assert "static 10.0.0.0/8" in out
        assert "cdn_ranges {" in out
        assert "provider cloudflare" in out
        assert "client_ip_headers Cf-Connecting-Ip" in out

    def test_compose_cdn_still_standalone(self):
        # Regression: Compose (the edge) keeps cdn_ranges standalone, no combine.
        out = _render_caddyfile(
            _crowdsec_active=True,
            _trusted_proxies_enabled=True,
            _tp_combine=False,
            _tp_dynamic=True,
            _tp_mode="cloudflare",
            _trusted_proxies_headers="Cf-Connecting-Ip",
            _trusted_proxies_strict=False,
            _trusted_proxies_cidrs=[],
        )
        assert "trusted_proxies cdn_ranges {" in out
        assert "combine" not in out


BOUNCER_ENROLL = os.path.join(
    REPO_ROOT, "roles", "crowdsec", "tasks", "bouncer_enroll.yml"
)


class TestBouncerEnrollForceGuard:
    """The auto-enroll path must ignore an ambient `force` so that
    `config set --force` (which only means "allow an unrecognized key") can't
    re-issue the bouncer on every restart and loop forever. `force` is a
    global extra-var shared between the two commands."""

    def _enroll_task(self):
        with open(BOUNCER_ENROLL) as f:
            tasks = yaml.safe_load(f)
        task = next(
            (t for t in tasks if t.get("name") == "Enroll the bouncer"), None
        )
        assert task is not None, "bouncer_enroll.yml must have the enroll task"
        return task

    def test_force_branch_is_gated_on_not_auto(self):
        when = " ".join(str(self._enroll_task().get("when", "")).split())
        # The force branch must be conjoined with `not bouncer_enroll_auto`.
        assert "force" in when and "bouncer_enroll_auto" in when, (
            "the enroll `when` must guard `force` with `bouncer_enroll_auto`"
        )
        assert "and not (bouncer_enroll_auto" in when, (
            "force must be ANDed with `not bouncer_enroll_auto` so the auto "
            "path ignores an ambient --force (the config-set/enroll loop)"
        )

    def test_no_key_and_no_bouncer_still_enroll(self):
        # The non-force re-enroll conditions must remain so a genuinely
        # un-enrolled instance (no bouncer / no stored key) still enrolls.
        when = " ".join(str(self._enroll_task().get("when", "")).split())
        assert "_crowdsec_existing" in when
        assert "_crowdsec_have_key" in when


