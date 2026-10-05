"""extension/skin enable runs update.php for every wiki the item was
enabled for: the named wiki with --wiki, every wiki without it."""

import os

import jinja2
import pytest
import yaml

ENABLE = os.path.join(
    os.path.dirname(__file__), "..", "..",
    "roles", "extensions_skins", "tasks", "enable.yml")


def _tasks():
    with open(ENABLE) as f:
        return yaml.safe_load(f)


def _update_task():
    return next(t for t in _tasks()
                if "update.php" in str((t.get("vars") or {}).get("exec_command", "")))


def _render(template, **context):
    env = jinja2.Environment()
    env.filters["quote"] = lambda s: "'%s'" % s
    return env.from_string(template).render(**context)


def test_update_command_never_passes_an_empty_wiki():
    cmd = _update_task()["vars"]["exec_command"]
    assert "default('')" not in cmd
    assert "_enable_wiki" in cmd


@pytest.mark.parametrize("wiki, expected", [
    ("draft", "['draft']"),
    ("", "['main', 'draft']"),
    (None, "['main', 'draft']"),
])
def test_update_runs_for_each_affected_wiki(wiki, expected):
    task = _update_task()
    assert task["loop_control"]["loop_var"] == "_enable_wiki"
    context = {"_enable_wikis": {"wiki_ids": ["main", "draft"]}}
    if wiki is not None:
        context["wiki"] = wiki
    assert _render(task["loop"].strip(), **context).strip() == expected


def test_wikis_are_read_only_without_wiki():
    read = next(t for t in _tasks() if "canasta_wikis_yaml" in t)
    assert read["register"] == "_enable_wikis"
    assert "wiki | default('') == ''" in read["when"]
