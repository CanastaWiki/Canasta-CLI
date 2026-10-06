"""gitops join refuses, before changing the instance, when the repo's wiki
farm has wikis the joining instance lacks: each wiki's url on the joining
host comes from the instance's own wikis.yaml."""

import os
import sys

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, os.path.join(REPO_ROOT, "filter_plugins"))
from canasta_gitops import canasta_wikis_template_ids  # noqa: E402

JOIN = os.path.join(REPO_ROOT, "roles", "gitops", "tasks", "join.yml")

TEMPLATE = """wikis:
- id: main
  url: {{wiki_url_main}}
  name: "Main Wiki"
- id: docs
  url: {{wiki_url_docs}}
  name: "docs"
  extra_databases:
  - docs_cargo
"""


def _tasks():
    with open(JOIN) as f:
        return yaml.safe_load(f)


def _index(tasks, name):
    return next(i for i, t in enumerate(tasks) if t.get("name") == name)


class TestTemplateIds:
    def test_reads_ids_in_order(self):
        assert canasta_wikis_template_ids(TEMPLATE) == ["main", "docs"]

    def test_ignores_extra_database_items(self):
        assert "docs_cargo" not in canasta_wikis_template_ids(TEMPLATE)

    def test_quoted_id(self):
        assert canasta_wikis_template_ids('wikis:\n- id: "main"\n') == ["main"]

    def test_empty(self):
        assert canasta_wikis_template_ids("") == []
        assert canasta_wikis_template_ids(None) == []


class TestJoinCheck:
    def test_check_runs_before_the_instance_is_changed(self):
        tasks = _tasks()
        check = _index(tasks, "Fail if this instance lacks some of the repo's wikis")
        assert check < _index(tasks, "Remove legacy .gitignore files")
        assert check < _index(
            tasks, "Reinit — wipe previous gitops state now that the clone succeeded")
        assert check < _index(tasks, "Move .git from temp clone to instance")

    def test_missing_wikis_are_template_ids_minus_local_ids(self):
        find = _tasks()[_index(_tasks(), "Find the repo's wikis this instance lacks")]
        expr = find["ansible.builtin.set_fact"]["_join_missing_wikis"]
        assert "canasta_wikis_template_ids" in expr
        assert "difference(_join_local_wikis.wiki_ids" in expr

    def test_message_names_the_add_command(self):
        fail = _tasks()[_index(_tasks(), "Fail if this instance lacks some of the repo's wikis")]
        msg = fail["ansible.builtin.fail"]["msg"]
        assert "canasta add -i {{ instance_id }} -w {{ w }}" in msg
        assert "Nothing was changed." in msg
