"""Regression guard for #1147: the Compose create memory preflight must accept a
~2 GiB host (Elasticsearch is no longer required) and only *warn* below ~8 GiB,
rather than hard-failing everything under the old 3500 MiB floor.
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
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


def _when_text(t):
    w = t.get("when")
    if not w:
        return ""
    return " ".join(str(x) for x in w) if isinstance(w, list) else str(w)


class TestMemoryPreflight:
    def setup_method(self):
        self.tasks = list(_walk(_load(PREFLIGHT)))

    def test_hard_floor_lowered_to_about_2gib(self):
        # A memory fail task gates on the host memory below ~2 GiB (1600 MiB),
        # not the old 4 GiB (3500 MiB) floor.
        mem_fails = [t for t in self.tasks
                     if ("ansible.builtin.fail" in t or "fail" in t)
                     and "_host_mem_mib" in _when_text(t)]
        assert mem_fails, "create_preflight must gate on measured host memory"
        assert any("1600" in _when_text(t) for t in mem_fails), (
            "the memory hard floor must be lowered to accept a ~2 GiB host "
            "(1600 MiB), not reject everything under 3500 MiB (#1147)")
        assert not any("3500" in _when_text(t) for t in mem_fails), (
            "the old 3500 MiB hard floor must be gone (#1147)")

    def test_warns_below_comfortable_threshold(self):
        warns = [t for t in self.tasks
                 if ("ansible.builtin.debug" in t or "debug" in t)
                 and "_host_mem_mib" in _when_text(t)
                 and "7000" in _when_text(t)]
        assert warns, (
            "create_preflight must WARN (not fail) when memory is below the "
            "comfortable ~8 GiB threshold (#1147)")
