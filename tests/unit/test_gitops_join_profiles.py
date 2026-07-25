"""Guard: gitops join must reconcile COMPOSE_PROFILES after rendering .env.

join renders .env verbatim from env.template + host vars, which carry no
COMPOSE_PROFILES literal; the reconcile (sync_compose_profiles) is what
materializes it from the feature flags and DB mode. Without it a freshly
joined instance's .env has no COMPOSE_PROFILES, leaving profile-gated services
unmanaged on a raw `docker compose up`. pull_compose.yml already does this;
join must match so both gitops entry points leave .env in the same state.
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
JOIN = os.path.join(REPO_ROOT, "roles", "gitops", "tasks", "join.yml")
PULL = os.path.join(REPO_ROOT, "roles", "gitops", "tasks", "pull_compose.yml")

RENDER = "render_compose.yml"
SYNC = "sync_compose_profiles.yml"


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


def _includes(tasks):
    """Ordered list of file basenames pulled in via include_tasks/role."""
    out = []
    for t in _walk(tasks):
        inc = t.get("ansible.builtin.include_tasks") or t.get("include_tasks")
        if isinstance(inc, dict):
            inc = inc.get("file", "")
        if inc:
            out.append(os.path.basename(str(inc).strip()))
        role = (t.get("ansible.builtin.include_role")
                or t.get("include_role"))
        if isinstance(role, dict) and role.get("tasks_from"):
            out.append(os.path.basename(str(role["tasks_from"]).strip()))
    return out


class TestJoinReconcilesProfiles:
    def test_join_syncs_compose_profiles(self):
        assert SYNC in _includes(_load(JOIN)), (
            "gitops join must reconcile COMPOSE_PROFILES (include "
            "sync_compose_profiles.yml) after rendering .env, or a joined "
            "instance's .env is left without COMPOSE_PROFILES"
        )

    def test_join_syncs_after_render(self):
        incs = _includes(_load(JOIN))
        assert RENDER in incs and SYNC in incs
        assert incs.index(SYNC) > incs.index(RENDER), (
            "the profile reconcile must run after render_compose so it "
            "materializes COMPOSE_PROFILES on the freshly rendered .env"
        )

    def test_matches_pull_which_already_reconciles(self):
        # Parity: both gitops entry points that render .env must reconcile it.
        assert SYNC in _includes(_load(PULL)), (
            "pull_compose.yml is the reference pattern and must reconcile "
            "COMPOSE_PROFILES; join is expected to mirror it"
        )
