"""Guards for capturing config/wikis.yaml edits into wikis.yaml.template.

config/wikis.yaml is a rendered file (gitignored); wikis.yaml.template is
the tracked source. A display name edited directly in config/wikis.yaml
must be captured back into the template, or it is dropped on the next
render and never reaches other hosts. A shared reconcile task does that
capture; these tests assert it exists, keeps `name` a literal while
`url` stays a placeholder, and is wired into init, `gitops add`, and
`config regenerate` in the right order.
"""

import os
import re

import pytest
import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
GITOPS_TASKS = os.path.join(REPO_ROOT, "roles", "gitops", "tasks")
RECONCILE = os.path.join(GITOPS_TASKS, "_reconcile_wikis_template.yml")
INIT_COMPOSE = os.path.join(GITOPS_TASKS, "init_compose.yml")
ADD_YML = os.path.join(GITOPS_TASKS, "add.yml")
REGENERATE = os.path.join(REPO_ROOT, "playbooks", "config_regenerate.yml")

RECONCILE_INCLUDE = "_reconcile_wikis_template.yml"


def _walk(tasks):
    for t in tasks or []:
        if not isinstance(t, dict):
            continue
        yield t
        for nested in ("block", "rescue", "always"):
            if nested in t:
                yield from _walk(t[nested])


def _load(path):
    with open(path) as f:
        return list(_walk(yaml.safe_load(f)))


def _include_path(task):
    inc = task.get("ansible.builtin.include_tasks") or task.get("include_tasks") or ""
    return inc if isinstance(inc, str) else str(inc)


def _flat_text(path):
    """Top-level task index of each task, for ordering assertions."""
    with open(path) as f:
        return yaml.safe_load(f)


def _copy_template_content():
    """Return the `content` the reconcile task writes to wikis.yaml.template,
    with YAML block-scalar indentation already stripped (so column offsets
    reflect the rendered template, not this file's source indentation)."""
    for task in _load(RECONCILE):
        copy = task.get("ansible.builtin.copy") or task.get("copy")
        if isinstance(copy, dict) and str(copy.get("dest", "")).endswith(
            "wikis.yaml.template"
        ):
            return copy["content"]
    raise AssertionError("no copy task writing wikis.yaml.template found")


class TestReconcileTask:
    def test_template_keeps_url_placeholder_and_name_literal(self):
        with open(RECONCILE) as f:
            content = f.read()
        # url is host-specific -> placeholder; name is shared -> literal.
        assert "wiki_url_{{ w.id }}" in content, "url must stay a placeholder"
        assert 'name: "{{ w.name | default(w.id) }}"' in content, (
            "name must be copied through as a literal so display-name edits "
            "are captured"
        )

    def test_acts_only_when_template_exists_or_creating(self):
        """Must no-op on K8s/non-gitops (no template) unless reconcile_create."""
        with open(RECONCILE) as f:
            content = f.read()
        assert "_reconcile_wikis_tmpl.stat.exists" in content
        assert "reconcile_create" in content

    def test_template_list_items_are_column_zero(self):
        """List items must sit at column 0 (block-sequence style matching
        canasta_wikis_yaml.py). render_compose.yml copies this indentation
        verbatim, and CanastaBase's config-subdir-wikis.sh parses it with
        column-0 line anchors; indenting here silently drops every
        public_assets rewrite rule (broken logos on wiki farms)."""
        content = _copy_template_content()
        assert "\n- id: " in content, (
            "wikis.yaml.template list items must start at column 0"
        )
        assert "\n  - id: " not in content, (
            "list items must not be indented; config-subdir-wikis.sh only "
            "parses column-0 '- id:' lines"
        )
        assert "\n  url: " in content and "\n    url: " not in content, (
            "mapping keys under a column-0 list item are indented 2 spaces"
        )


class TestExtraDatabasesSurviveRender:
    """A wiki's extra_databases declaration (the Cargo database that must
    be dumped in the same transaction as the wiki) is a shared literal
    field. An emitter that does not know about it drops it at the next
    render, and the Cargo database silently stops being captured."""

    def _render(self, wikis, db_groups):
        jinja2 = pytest.importorskip("jinja2")
        template = jinja2.Template(
            _copy_template_content(), trim_blocks=True, lstrip_blocks=False)
        rendered = template.render(_reconcile_wikis_data={
            "wikis": wikis, "db_groups": db_groups})
        return rendered

    def _rendered_yaml(self, wikis, db_groups):
        """Parse the template the way render_compose.yml sees it: with the
        host-specific url placeholders already substituted."""
        rendered = self._render(wikis, db_groups)
        return yaml.safe_load(
            re.sub(r"\{\{wiki_url_[^}]+\}\}", "https://example.com", rendered))

    def test_declaration_is_carried_into_the_template(self):
        parsed = self._rendered_yaml(
            [{"id": "main", "name": "Main"}],
            [{"wiki": "main", "databases": ["main", "main_cargo"]}])
        assert parsed["wikis"][0]["extra_databases"] == ["main_cargo"]

    def test_wiki_without_extras_gets_no_empty_key(self):
        parsed = self._rendered_yaml(
            [{"id": "main", "name": "Main"}],
            [{"wiki": "main", "databases": ["main"]}])
        assert "extra_databases" not in parsed["wikis"][0]

    def test_only_the_declaring_wiki_gets_the_key(self):
        parsed = self._rendered_yaml(
            [{"id": "one", "name": "One"}, {"id": "two", "name": "Two"}],
            [{"wiki": "one", "databases": ["one", "one_cargo"]},
             {"wiki": "two", "databases": ["two"]}])
        assert parsed["wikis"][0]["extra_databases"] == ["one_cargo"]
        assert "extra_databases" not in parsed["wikis"][1]

    def test_list_items_still_start_at_column_zero(self):
        rendered = self._render(
            [{"id": "main", "name": "Main"}],
            [{"wiki": "main", "databases": ["main", "main_cargo"]}])
        assert rendered.startswith("wikis:\n- id: main")
        assert "\n  - id: " not in rendered


class TestWiring:
    def test_init_uses_shared_reconcile_not_a_second_generator(self):
        with open(INIT_COMPOSE) as f:
            text = f.read()
        assert RECONCILE_INCLUDE in text, "init must use the shared reconcile task"
        # No second inline generator writing the template (drift risk).
        assert text.count("dest: \"{{ instance_path }}/wikis.yaml.template\"") == 0, (
            "init must not inline a second wikis.yaml.template generator"
        )

    def test_add_reconciles_before_staging_and_stages_template(self):
        raw = _flat_text(ADD_YML)
        cmds = " ".join(str(t) for t in raw)
        # reconcile include present
        assert any(RECONCILE_INCLUDE in _include_path(t) for t in _walk(raw))
        # template explicitly staged (it lives outside config/)
        assert "git add -- wikis.yaml.template" in cmds
        # reconcile must run before the template is staged (it writes the
        # template the stage then captures)
        recon_idx = next(
            i for i, t in enumerate(raw)
            if isinstance(t, dict) and RECONCILE_INCLUDE in _include_path(t)
        )
        stage_idx = next(
            i for i, t in enumerate(raw)
            if isinstance(t, dict) and "git add -- wikis.yaml.template" in str(t)
        )
        assert recon_idx < stage_idx, "reconcile must run before staging template"

    def test_template_staged_whenever_it_exists_not_only_on_capture(self):
        """A template edit captured by an earlier 'config regenerate' is
        left unstaged unless 'gitops add' stages the template whenever it
        exists — not only when this reconcile run changed it."""
        raw = _flat_text(ADD_YML)
        stage = next(
            t for t in _walk(raw)
            if "git add -- wikis.yaml.template" in str(
                (t.get("ansible.builtin.command") or t.get("command") or {})
            )
        )
        when = str(stage.get("when", ""))
        assert "stat.exists" in when, "template stage must be gated on existence"
        assert "_reconcile_wikis_write" not in when, (
            "template stage must not be gated on whether this reconcile run "
            "changed it, or a regenerate-then-add leaves the edit unstaged"
        )

    def test_regenerate_captures_before_rerender(self):
        raw = _flat_text(REGENERATE)
        recon_idx = next(
            (i for i, t in enumerate(raw)
             if isinstance(t, dict) and RECONCILE_INCLUDE in _include_path(t)),
            None,
        )
        render_idx = next(
            (i for i, t in enumerate(raw)
             if isinstance(t, dict) and "render_gitops_config.yml" in _include_path(t)),
            None,
        )
        assert recon_idx is not None, "regenerate must capture wikis.yaml edits"
        assert render_idx is not None
        assert recon_idx < render_idx, (
            "capture must run before re-render or regenerate clobbers the edit"
        )
