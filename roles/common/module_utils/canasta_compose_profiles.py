# The COMPOSE_PROFILES and CANASTA_CADDY_IMAGE a Compose instance's .env
# should hold. Shared by direct_commands/_helpers.py and, through the
# canasta_compose_profiles filters, roles/orchestrator/tasks/
# sync_compose_profiles.yml.

# Derived like the feature profiles, but from USE_EXTERNAL_DB.
INTERNAL_DB = "internal-db"


def _flag_on(env, key, default="false"):
    # `or default` rather than dict.get's default: a key present with an
    # empty value is the host storing no opinion, not "off".
    return (env.get(key) or default).strip().lower() == "true"


def managed_profile_names(managed_profiles):
    """Every profile name the reconciliation owns, internal-db included."""
    return {p["profile"] for p in managed_profiles} | {INTERNAL_DB}


def desired_profiles(env, current, managed_profiles):
    """The profiles in `current` that Canasta does not manage, then each
    managed profile whose flag is on, then internal-db unless an external
    database is configured. `managed_profiles` is the
    canasta_managed_profiles list from vars/compose_profiles.yml."""
    owned = managed_profile_names(managed_profiles)
    desired = [p for p in current if p not in owned]
    for p in managed_profiles:
        if _flag_on(env, p["flag"], p["default"]):
            desired.append(p["profile"])
    if not _flag_on(env, "USE_EXTERNAL_DB"):
        desired.append(INTERNAL_DB)
    return desired


def desired_caddy_image(env, plugin_image):
    """The CANASTA_CADDY_IMAGE value: the plugin image while CrowdSec is on,
    empty (stock Caddy) otherwise. A value outside the plugin image's
    repository is the operator's own and is returned unchanged; any tag of
    that repository is Canasta's, so an older one is moved to plugin_image."""
    current = env.get("CANASTA_CADDY_IMAGE") or ""
    repository = plugin_image.rsplit(":", 1)[0] + ":"
    if current and not current.startswith(repository):
        return current
    return plugin_image if _flag_on(env, "CANASTA_ENABLE_CROWDSEC") else ""
