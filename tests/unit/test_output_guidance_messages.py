"""Wording of two messages whose fix is in the text itself.

- A pull that stops on a missing wiki_url_<id> var says where to add it and
  that it must be shared with gitops add and push before pulling again;
  editing it in place and re-running the pull only meets the dirty-tree
  refusal.
- config refresh-template --apply reports its outcome without a trailer
  telling the operator to capture the change into gitops.
"""

import os

import pytest
import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
PULL = os.path.join(REPO_ROOT, "roles", "gitops", "tasks", "pull_compose.yml")
REFRESH = os.path.join(REPO_ROOT, "roles", "config", "tasks",
                       "refresh_template.yml")


def _find(items, name):
    for t in items or []:
        if not isinstance(t, dict):
            continue
        if t.get("name") == name:
            return t
        for key in ("block", "always", "rescue"):
            found = _find(t.get(key), name)
            if found:
                return found
    return None


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f)


def test_missing_wiki_url_says_to_share_the_var_before_pulling_again():
    jinja2 = pytest.importorskip("jinja2")
    task = _find(_load(PULL),
                 "Fail if any wiki url rendered empty (missing wiki_url_<id> var)")
    assert task is not None, "the empty-url refusal is gone or renamed"
    msg = jinja2.Environment().from_string(
        task["ansible.builtin.fail"]["msg"]).render(
            _pull_empty_url_wikis=["docs"], _pull_hostname="h2")
    assert "'wiki_url_docs' to hosts/h2/vars.yaml" in msg
    assert "'canasta gitops add hosts/h2/vars.yaml'" in msg
    assert "'canasta gitops push'" in msg
    assert msg.index("gitops push") < msg.index("'canasta gitops pull' again")


def test_refresh_template_apply_has_no_gitops_trailer():
    for task in _load(REFRESH):
        for nested in [task] + (task.get("block") or []):
            debug = nested.get("ansible.builtin.debug")
            if debug:
                assert "gitops" not in str(debug.get("msg")), nested["name"]
