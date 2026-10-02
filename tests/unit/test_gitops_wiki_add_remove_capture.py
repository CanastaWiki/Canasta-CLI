"""add and remove keep a Compose gitops repo consistent with the wikis.

add captures a new wiki into wikis.yaml.template as {{wiki_url_<id>}}, so it
must also record wiki_url_<id> in this host's vars or the next render
refuses to write config/wikis.yaml. remove must undo the capture: drop the
wiki from the template and its var from the host vars, and stage the
deletion of its tracked paths, or the template renders it back and the
deleted files block the next pull.
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
GITOPS = os.path.join(REPO_ROOT, "roles", "gitops", "tasks")


def _walk(tasks):
    for t in tasks or []:
        if not isinstance(t, dict):
            continue
        yield t
        for key in ("block", "rescue", "always"):
            yield from _walk(t.get(key))


def _tasks(path):
    with open(path) as f:
        return list(_walk(yaml.safe_load(f)))


def _named(path, name):
    return next(t for t in _tasks(path) if t.get("name") == name)


def test_add_records_the_wiki_url_var():
    path = os.path.join(GITOPS, "stage_new_wiki.yml")
    record = _named(path, "Record the new wiki's url in this host's vars")
    assert record["ansible.builtin.include_tasks"].endswith(
        "/roles/config/tasks/_update_gitops_wiki_url.yml")
    assert "new_wiki_id" in record["vars"]["_gw_wiki"]
    names = [t.get("name") for t in _tasks(path)]
    assert names.index("Capture wikis.yaml into the template") < \
        names.index("Record the new wiki's url in this host's vars")


def test_remove_runs_the_undo():
    with open(os.path.join(REPO_ROOT, "playbooks", "remove.yml")) as f:
        tasks = yaml.safe_load(f)
    names = [t.get("name") for t in tasks]
    undo = tasks[names.index("Remove the wiki from gitops")]
    assert undo["ansible.builtin.include_tasks"].endswith(
        "/roles/gitops/tasks/unstage_removed_wiki.yml")
    assert undo["vars"]["removed_wiki_id"] == "{{ wiki }}"
    assert names.index("Remove wiki from wikis.yaml") < \
        names.index("Remove the wiki from gitops")


def test_remove_regenerates_the_template_and_drops_the_var():
    path = os.path.join(GITOPS, "unstage_removed_wiki.yml")
    regen = _named(path, "Regenerate wikis.yaml.template without the wiki")
    assert regen["ansible.builtin.include_tasks"].endswith(
        "/_reconcile_wikis_template.yml")
    write = _named(path, "Write vars.yaml without the wiki's url")
    assert "rejectattr('key', 'equalto', 'wiki_url_' ~ removed_wiki_id)" in \
        write["ansible.builtin.copy"]["content"]
    assert write["no_log"] is True


def test_remove_stages_the_deletion_of_tracked_paths():
    rm = _named(os.path.join(GITOPS, "unstage_removed_wiki.yml"),
                "Stage the deletion of the wiki's tracked paths")
    argv = rm["ansible.builtin.command"]["argv"]
    assert argv[:2] == ["git", "rm"]
    assert "--cached" in argv and "--ignore-unmatch" in argv
    assert "public_assets/{{ removed_wiki_id }}" in argv
    assert "config/settings/wikis/{{ removed_wiki_id }}" in argv
