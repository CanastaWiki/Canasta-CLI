"""`canasta extension|skin remove` reports name the item type once, in the
singular for one item and the plural otherwise, and say nothing more when
nothing was present to remove.
"""

import os

import pytest
import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
REMOVE = os.path.join(
    REPO_ROOT, "roles", "extensions_skins", "tasks", "remove.yml")


def _task(name):
    with open(REMOVE) as f:
        tasks = yaml.safe_load(f)
    for t in tasks:
        if isinstance(t, dict) and t.get("name") == name:
            return t
    raise AssertionError("task %r not found in remove.yml" % name)


def _env():
    jinja2 = pytest.importorskip("jinja2")
    from ansible.plugins.filter.core import FilterModule
    from ansible.plugins.test.core import TestModule

    env = jinja2.Environment()
    env.filters.update(FilterModule().filters())
    env.tests.update(TestModule().tests())
    return env


def _render(expr, **ctx):
    return " ".join(_env().from_string(expr).render(**ctx).split())


def _when(task, **ctx):
    conds = task.get("when", [])
    if not isinstance(conds, list):
        conds = [conds]
    env = _env()
    return all(env.compile_expression(str(c))(**ctx) for c in conds)


ABSENT = "Report absent items skipped"
REMOVED = "Report {{ _item_type }} removed"


def _ctx(item_type, present, absent=(), enabled=(), gitops=False):
    return {
        "_item_type": item_type,
        "_item_dir": item_type,
        "_remove_present": list(present),
        "_remove_absent": list(absent),
        "_remove_enabled": list(enabled),
        "is_gitops": gitops,
    }


@pytest.mark.parametrize("item_type,singular", [
    ("skins", "skin"), ("extensions", "extension")])
class TestRemovedReport:
    def test_one_item_is_singular(self, item_type, singular):
        msg = _task(REMOVED)["ansible.builtin.debug"]["msg"]
        text = _render(msg, **_ctx(item_type, ["A"]))
        assert text.startswith("Removed %s: A." % singular), text

    def test_several_items_are_plural(self, item_type, singular):
        msg = _task(REMOVED)["ansible.builtin.debug"]["msg"]
        text = _render(msg, **_ctx(item_type, ["A", "B"]))
        assert text.startswith("Removed %s: A, B." % item_type), text

    def test_silent_when_nothing_present(self, item_type, singular):
        ctx = _ctx(item_type, [], absent=["A"])
        assert _when(_task(ABSENT), **ctx)
        assert not _when(_task(REMOVED), **ctx), (
            "the absent-items report already covers this case")


@pytest.mark.parametrize("item_type,singular", [
    ("skins", "Skin"), ("extensions", "Extension")])
class TestAbsentReport:
    def test_one_item_is_singular(self, item_type, singular):
        msg = _task(ABSENT)["ansible.builtin.debug"]["msg"]
        text = _render(msg, **_ctx(item_type, [], absent=["A"]))
        assert text == "%s not present under %s/ (nothing to remove): A" % (
            singular, item_type), text

    def test_several_items_are_plural(self, item_type, singular):
        msg = _task(ABSENT)["ansible.builtin.debug"]["msg"]
        text = _render(msg, **_ctx(item_type, [], absent=["A", "B"]))
        assert text == "%s not present under %s/ (nothing to remove): A, B" % (
            item_type.capitalize(), item_type), text
