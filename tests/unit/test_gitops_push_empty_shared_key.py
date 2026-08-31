"""An empty shared key must not be migrated into hosts/_shared/vars.yaml.

`gitops push` moves every gitops_shared_keys entry out of the pushing host's
vars.yaml and into the shared file, where its value becomes the default for
every other host. A host whose value is empty — never set, or cleared — would
therefore replace a working credential everywhere with "". Nothing surfaces on
the pushing host: the failure appears on another host's next pull, as a
rendered .env missing a key it had.

An empty value means this host stores no opinion. The .env render already
reads it that way, omitting the line rather than writing KEY=.
"""
import os

import yaml


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PUSH_COMPOSE = os.path.join(
    REPO_ROOT, "roles", "gitops", "tasks", "push_compose.yml",
)


def _walk(tasks):
    for task in tasks or []:
        yield task
        for key in ("block", "rescue", "always"):
            for nested in _walk(task.get(key)):
                yield nested


def _named(name):
    with open(PUSH_COMPOSE) as fh:
        tasks = yaml.safe_load(fh)
    for task in _walk(tasks):
        if (task.get("name") or "") == name:
            return task
    return None


def test_empty_and_null_values_are_not_migrated():
    task = _named("Identify keys to migrate")
    assert task, "expected the migration candidate list to be built"
    expr = str(task["ansible.builtin.set_fact"]["_push_keys_to_migrate"])
    assert "rejectattr('value', 'none')" in expr, (
        "a key present but unset would migrate as null"
    )
    assert "rejectattr('value', 'equalto', '')" in expr, (
        "a key present but empty would blank the shared value"
    )


def test_only_migrated_keys_are_stripped_from_host_vars():
    # A shared key held back because it is empty must stay in the host's
    # vars.yaml: host-wins on merge is what lets it keep meaning "no value
    # here" once the shared file carries someone else's value.
    task = _named("Remove migrated keys from host vars")
    assert task, "expected the host-vars rewrite"
    expr = str(task["ansible.builtin.copy"]["content"])
    assert "_push_keys_to_migrate" in expr, (
        "stripping every gitops_shared_keys entry drops the held-back key too"
    )
    assert "rejectattr('key', 'in', gitops_shared_keys)" not in expr


def test_replacing_a_shared_value_is_reported():
    task = _named("Report shared values this push replaces")
    assert task, "overwriting every other host's value must not be silent"
    msg = str(task["ansible.builtin.debug"]["msg"])
    assert "_push_keys_overwriting" in msg
    assert "gitops pull" in msg, "the message must say when other hosts see it"
    # Names only: push output is not a place to print credentials.
    assert "_push_shared_vars[" not in msg
    assert "attribute='value'" not in msg


def test_the_overwrite_list_excludes_unchanged_values():
    task = _named("Identify shared values this push replaces")
    assert task, "expected the overwrite list to be built"
    expr = str(task["ansible.builtin.set_fact"]["_push_keys_overwriting"])
    # A push that re-migrates the identical pair changes nothing and must
    # not claim it replaced anything.
    assert "difference(_push_keys_to_migrate)" in expr


RENDER_COMPOSE = os.path.join(
    REPO_ROOT, "roles", "gitops", "tasks", "render_compose.yml",
)


def test_a_null_var_renders_as_unset_not_as_blank():
    # Holding an empty key back leaves it in vars.yaml, so the render is now
    # the only thing standing between a stored null and a written "KEY=".
    # `| default('')` does not fire on None, and Ansible prints None as the
    # empty string, so without the explicit test the line is written blank —
    # the state the surrounding comment exists to prevent.
    with open(RENDER_COMPOSE) as fh:
        tasks = yaml.safe_load(fh)
    task = next(
        t for t in _walk(tasks)
        if (t.get("name") or "") == "Render .env from template"
    )
    body = str(task["ansible.builtin.copy"]["content"])
    assert "value is not none" in body
