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
        names_in_order = [t.get("name", "") for t in raw if isinstance(t, dict)]
        cmds = " ".join(str(t) for t in raw)
        # reconcile include present
        assert any(RECONCILE_INCLUDE in _include_path(t) for t in _walk(raw))
        # template explicitly staged (it lives outside config/)
        assert "git add -- wikis.yaml.template" in cmds
        # reconcile must come before the config staging
        recon_idx = next(
            i for i, t in enumerate(raw)
            if isinstance(t, dict) and RECONCILE_INCLUDE in _include_path(t)
        )
        stage_idx = next(
            i for i, t in enumerate(raw)
            if isinstance(t, dict) and "git add -A -- config" in str(t)
        )
        assert recon_idx < stage_idx, "reconcile must run before staging config/"

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
