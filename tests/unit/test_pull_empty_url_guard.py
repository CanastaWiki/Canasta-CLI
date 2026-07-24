"""Regression guard for #1177: `gitops pull` must not overwrite config/wikis.yaml
with a blank url.

pull_compose.yml renders wikis.yaml from wikis.yaml.template with the same
`{{wiki_url_<id>}} | default('')` pattern as render_compose.yml. If a wiki's
wiki_url_<id> var is absent on the pulling host, the url renders empty and
MediaWiki's FarmConfigLoader fatals on every request. pull must render into a
fact and refuse to write when any url came out empty (sibling of #1164).
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
PULL_COMPOSE = os.path.join(
    REPO_ROOT, "roles", "gitops", "tasks", "pull_compose.yml")


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


class TestPullRefusesEmptyUrl:
    def test_fail_task_guards_empty_url(self):
        guards = [
            t for t in _walk(_load(PULL_COMPOSE))
            if ("ansible.builtin.fail" in t or "fail" in t)
            and "rejectattr('url')" in yaml.safe_dump(t)
        ]
        assert guards, (
            "pull_compose.yml must fail (before writing config/wikis.yaml) when "
            "a wiki url rendered empty — rejectattr('url') on the rendered "
            "wikis list (#1177)")

    def test_wikis_yaml_written_from_validated_fact(self):
        text = open(PULL_COMPOSE).read()
        assert "_pull_wikis_content" in text, (
            "pull_compose.yml must render wikis.yaml into a fact "
            "(_pull_wikis_content) so it can be validated before it replaces "
            "the live file (#1177)")
