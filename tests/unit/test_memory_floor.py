"""Regression guard for #1147: the host memory floor is profile-aware and
enforced in both `create` and `config set`.

Elasticsearch and the observability stack each raise the requirement, so the
floor is ~2 GiB (neither) / ~4 GiB (either) / ~8 GiB (both), expressed as
1600 / 3500 / 7000 MiB (below the marketed size to absorb the kernel/firmware
reporting gap). The shared check lives in check_host_memory.yml and is included
by create (once .env is finalized) and by config set (before enabling either
profile). The old flat 3500 MiB preflight floor must be gone.
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
CHECK = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "check_host_memory.yml")
ENV_UPDATE = os.path.join(
    REPO_ROOT, "roles", "create", "tasks", "_env_update.yml")
SIDE_EFFECTS = os.path.join(
    REPO_ROOT, "roles", "config", "tasks", "_side_effects.yml")
PREFLIGHT = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "create_preflight.yml")


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f) or []


def _walk(tasks):
    for t in tasks or []:
        if not isinstance(t, dict):
            continue
        yield t
        for nested in ("block", "rescue", "always"):
            if nested in t:
                yield from _walk(t[nested])


def _includes(path):
    return [
        (t.get("ansible.builtin.include_tasks") or t.get("include_tasks") or "")
        for t in _walk(_load(path))
    ]


def _when_text(t):
    w = t.get("when")
    if not w:
        return ""
    return " ".join(str(x) for x in w) if isinstance(w, list) else str(w)


class TestCheckHostMemory:
    def test_profile_aware_floors_present(self):
        text = open(CHECK).read()
        for mib in ("7000", "3500", "1600"):
            assert mib in text, (
                "check_host_memory.yml must encode the %s MiB floor "
                "(~2/4/8 GiB by profile) (#1147)" % mib)

    def test_has_a_fail_gated_on_measured_memory(self):
        fails = [t for t in _walk(_load(CHECK))
                 if ("ansible.builtin.fail" in t or "fail" in t)
                 and "_mem_floor_mib" in _when_text(t)]
        assert fails, (
            "check_host_memory.yml must fail when measured memory is below the "
            "required floor (#1147)")


class TestWiredIntoCreateAndConfigSet:
    def test_create_enforces_floor(self):
        assert any("check_host_memory.yml" in inc for inc in _includes(ENV_UPDATE)), (
            "create (_env_update.yml) must include check_host_memory.yml once "
            ".env is finalized (#1147)")

    def test_config_set_enforces_on_enable(self):
        assert any("check_host_memory.yml" in inc for inc in _includes(SIDE_EFFECTS)), (
            "config set (_side_effects.yml) must include check_host_memory.yml "
            "when enabling a profile (#1147)")
        # The include must be reachable only when enabling ES/observability.
        text = open(SIDE_EFFECTS).read()
        assert "CANASTA_ENABLE_ELASTICSEARCH" in text
        assert "CANASTA_ENABLE_OBSERVABILITY" in text


class TestOldFlatFloorRemoved:
    def test_preflight_no_longer_hard_fails_at_3500(self):
        text = open(PREFLIGHT).read()
        assert "3500" not in text, (
            "the old flat 3500 MiB preflight floor must be removed — the "
            "profile-aware check replaces it (#1147)")
