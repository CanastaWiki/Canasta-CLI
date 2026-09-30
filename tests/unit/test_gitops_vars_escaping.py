"""gitops init writes vars.yaml values escaped.

vars.yaml was rendered as `key: "{{ value }}"`, so a value containing `"` or
`\\` made the file unparseable (every later push failed) and a `\\n` or `\\t`
sequence decoded to a different character. Values now go through to_json.
"""

import os

import pytest
import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
INIT = os.path.join(REPO_ROOT, "roles", "gitops", "tasks", "init_compose.yml")
RECONCILE = os.path.join(REPO_ROOT, "roles", "gitops", "tasks",
                         "_reconcile_wikis_template.yml")

AWKWARD = [
    'plain',
    'with "double" quotes',
    'back\\slash',
    'looks like an escape \\n\\t',
    "single ' quote",
    'dollar $and #hash: colon',
    'unicode café',
]


def _task(path, name):
    def walk(tasks):
        for t in tasks:
            if t.get("name") == name:
                return t
            for key in ("block", "rescue", "always"):
                found = walk(t.get(key, []))
                if found:
                    return found
        return None
    with open(path) as f:
        return walk(yaml.safe_load(f))


@pytest.mark.parametrize("value", AWKWARD)
def test_vars_yaml_round_trips(tmp_path, value):
    task = _task(INIT, "Build vars content (placeholder values + wiki URLs + admin passwords)")
    (tmp_path / "admin-password_main").write_text(value)
    templar = Templar(loader=DataLoader(), variables={
        "_gitops_env": {"variables": {"MYSQL_PASSWORD": value, "HTTP_PORT": "80"}},
        "canasta_secret_key_regex": "PASSWORD",
        "canasta_host_specific_nonsecret": ["HTTP_PORT"],
        "_gitops_wikis": {"wikis": [{"id": "main", "url": value}],
                          "wiki_ids": ["main"]},
        "instance_path": str(tmp_path),
    })
    rendered = templar.template(
        trust_as_template(task["ansible.builtin.copy"]["content"]))
    data = yaml.safe_load(rendered)
    assert data["mysql_password"] == value
    assert data["http_port"] == "80"
    assert data["wiki_url_main"] == value
    assert data["admin_password_main"] == value


@pytest.mark.parametrize("value", AWKWARD)
def test_wikis_template_site_name_round_trips(value):
    with open(RECONCILE) as f:
        text = f.read()
    assert 'name: {{ w.name | default(w.id) | to_json }}' in text
    templar = Templar(loader=DataLoader(), variables={"w": {"id": "main", "name": value}})
    line = templar.template(trust_as_template(
        "name: {{ w.name | default(w.id) | to_json }}"))
    assert yaml.safe_load(line)["name"] == value
