"""Alias hostnames in wikis.yaml, which the HAProxy edge redirects to a wiki.

doctor names Caddyfile.global site blocks an alias has made redundant, and
under gitops the aliases come from wikis.yaml.template rather than edits to
config/wikis.yaml.
"""

import os
import re
import sys

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, REPO_ROOT)

from direct_commands import doctor  # noqa: E402


def _instance(tmp_path, wikis, global_=""):
    config = tmp_path / "config"
    config.mkdir(parents=True)
    (config / "wikis.yaml").write_text(yaml.safe_dump({"wikis": wikis}))
    (config / "Caddyfile.global").write_text(global_)
    return str(tmp_path)


def _lines(path, orchestrator="compose"):
    return doctor._caddy_alias_lines({"id": "one", "path": path,
                                      "orchestrator": orchestrator})


WIKIS = [{"id": "main", "url": "example.com", "aliases": ["www.example.com"]}]


def test_doctor_names_a_caddyfile_global_block_an_alias_replaced(tmp_path):
    path = _instance(tmp_path, WIKIS, "WWW.example.com {\n"
                     "    redir https://example.com{uri} permanent\n}\n"
                     "get.example.com {\n    respond ok\n}\n")
    out = "\n".join(_lines(path))
    assert "WARN" in out
    assert re.findall(r"site block for (\S+) is never reached", out) == ["www.example.com"]


def test_doctor_leaves_bad_aliases_to_the_edge(tmp_path):
    bad = [{"id": "main", "url": "example.com", "aliases": "www.example.com"}]
    assert _lines(_instance(tmp_path, bad, "www.example.com {\n}\n")) == []


def test_doctor_is_quiet_without_overlap(tmp_path):
    assert _lines(_instance(tmp_path / "a", WIKIS, "get.example.com {\n}\n")) == []
    assert _lines(_instance(tmp_path / "b", [{"id": "main", "url": "example.com"}],
                            "www.example.com {\n}\n")) == []


# --- gitops ------------------------------------------------------------- #

import base64  # noqa: E402
import json  # noqa: E402

import jinja2  # noqa: E402

sys.path.insert(0, os.path.join(REPO_ROOT, "filter_plugins"))
import canasta_gitops as g  # noqa: E402

TASKS = os.path.join(REPO_ROOT, "roles")

TEMPLATE = """wikis:
- id: main
  url: {{wiki_url_main}}
  name: "Main"
  aliases:
    - "{{wiki_alias_main_www}}"
    - "{{wiki_alias_main_org}}"
"""
VARS = {"wiki_url_main": "example.com", "wiki_alias_main_www": "www.example.com"}


def _live(*aliases):
    wiki = {"id": "main", "url": "example.com", "name": "Main"}
    if aliases:
        wiki["aliases"] = list(aliases)
    return yaml.safe_dump({"wikis": [wiki]})


def _task(path, name):
    with open(os.path.join(TASKS, *path.split("/"))) as f:
        tasks = yaml.safe_load(f)
    stack = list(tasks)
    while stack:
        t = stack.pop()
        if t.get("name") == name:
            return t
        for key in ("block", "rescue", "always"):
            stack += t.get(key, [])
    raise AssertionError("no task %r in %s" % (name, path))


def test_placeholders_render_as_the_gitops_render_writes_them():
    content = _task("gitops/tasks/render_compose.yml",
                    "Render wikis.yaml content from template")
    body = content["ansible.builtin.set_fact"]["_render_wikis_content"]
    body = body.replace("_render_wikis_template_raw.content | b64decode", "src")
    env = jinja2.Environment(trim_blocks=True)
    rendered = env.from_string(body).render(src=TEMPLATE, _render_vars=VARS)
    assert g._render_placeholders(TEMPLATE, VARS).strip() == rendered.strip()
    doc = yaml.safe_load(rendered)
    assert doc["wikis"][0]["aliases"] == ["www.example.com", ""]


def test_alias_differences_alone_are_not_a_template_edit():
    assert g.canasta_wikis_sync_action(_live("www.example.com"), TEMPLATE, TEMPLATE) == "none"


def test_rendered_aliases_are_not_edits():
    assert g.canasta_wikis_alias_edits(_live("www.example.com"), TEMPLATE,
                                       TEMPLATE, VARS, VARS) == []


def test_aliases_edited_in_the_live_file_are_found():
    assert g.canasta_wikis_alias_edits(_live("www.example.com", "example.net"),
                                       TEMPLATE, TEMPLATE, VARS, VARS) == ["main"]
    assert g.canasta_wikis_alias_edits(_live(), TEMPLATE, TEMPLATE, VARS, VARS) == ["main"]
    plain = TEMPLATE.split("  aliases:")[0]
    assert g.canasta_wikis_alias_edits(_live("www.example.com"), plain, plain,
                                       VARS, VARS) == ["main"]


def test_edits_to_the_vars_or_template_are_not_live_edits():
    new_vars = dict(VARS, wiki_alias_main_org="example.org")
    # The live file still holds what the staged sources render to.
    assert g.canasta_wikis_alias_edits(_live("www.example.com"), TEMPLATE,
                                       TEMPLATE, new_vars, VARS) == []
    plain = TEMPLATE.split("  aliases:")[0]
    assert g.canasta_wikis_alias_edits(_live(), TEMPLATE, plain, VARS, VARS) == []


def test_without_a_staged_template_nothing_is_held_to_it():
    assert g.canasta_wikis_alias_edits(_live("www.example.com"), TEMPLATE, "",
                                       VARS, {}) == []


def test_the_record_holds_the_aliases_the_live_file_last_agreed_on():
    record = g.canasta_wikis_record(_live("www.example.com"))
    assert json.loads(record) == {"aliases": {"main": ["www.example.com"]},
                                  "urls": {"main": "example.com"}}
    assert g.canasta_wikis_record("- not a wikis.yaml") == ""
    # A vars change already staged has not reached the live file yet.
    new_vars = dict(VARS, wiki_alias_main_www="www.example.org")
    assert g.canasta_wikis_alias_edits(_live("www.example.com"), TEMPLATE,
                                       TEMPLATE, new_vars, new_vars, record) == []


def test_aliases_that_differ_from_the_record_and_the_sources_are_edits():
    record = g.canasta_wikis_record(_live("www.example.com"))
    assert g.canasta_wikis_alias_edits(_live("example.net"), TEMPLATE,
                                       TEMPLATE, VARS, VARS, record) == ["main"]
    # The live file as the sources render it now is never an edit.
    new_vars = dict(VARS, wiki_alias_main_www="www.example.org")
    assert g.canasta_wikis_alias_edits(_live("www.example.org"), TEMPLATE,
                                       TEMPLATE, new_vars, new_vars, record) == []


def test_an_unreadable_record_falls_back_to_the_staged_sources():
    assert g.canasta_wikis_alias_edits(_live("example.net"), TEMPLATE, TEMPLATE,
                                       VARS, VARS, "not json") == ["main"]
    assert g.canasta_wikis_alias_edits(_live("www.example.com"), TEMPLATE, TEMPLATE,
                                       VARS, VARS, "") == []


def test_the_record_is_written_wherever_config_wikis_yaml_is_rendered():
    for path, before in (("gitops/tasks/render_compose.yml", "Render wikis.yaml from template"),
                         ("gitops/tasks/sync_wikis_yaml.yml",
                          "Stage the template once it agrees with config/wikis.yaml")):
        with open(os.path.join(TASKS, *path.split("/"))) as f:
            tasks = yaml.safe_load(f)
        names = [t.get("name") for t in tasks]
        record = [i for i, t in enumerate(tasks)
                  if "_record_wikis.yml" in str(t.get("ansible.builtin.include_tasks"))]
        assert record and record[0] > names.index(before), path
    with open(os.path.join(TASKS, "gitops", "files", "gitignore.default")) as f:
        assert ".gitops-wikis-record" in f.read().split()


def _capture(live_wikis, template=None):
    task = _task("gitops/tasks/_reconcile_wikis_template.yml",
                 "Write wikis.yaml.template from live wikis.yaml")
    env = jinja2.Environment(trim_blocks=True, lstrip_blocks=False)
    env.filters["to_json"] = json.dumps
    env.filters["b64decode"] = lambda v: base64.b64decode(v).decode()
    env.filters["canasta_wikis_template_aliases"] = g.canasta_wikis_template_aliases
    raw = {"content": base64.b64encode(template.encode()).decode()} if template else {}
    return env.from_string(task["ansible.builtin.copy"]["content"]).render(
        _reconcile_wikis_data={"wikis": live_wikis,
                               "db_groups": [{"databases": [w["id"]]} for w in live_wikis]},
        _reconcile_wikis_tmpl={"stat": {"exists": template is not None}},
        _reconcile_wikis_tmpl_raw=raw)


def test_capture_keeps_the_templates_own_alias_placeholders():
    live = [{"id": "main", "name": "Main", "aliases": ["www.example.com"]},
            {"id": "docs", "name": "Docs", "aliases": ["docs.example.org"]}]
    out = _capture(live, TEMPLATE)
    doc = yaml.safe_load(re.sub(r"\{\{wiki_url_\w+\}\}", "x", out))
    assert doc["wikis"][0]["aliases"] == ["{{wiki_alias_main_www}}",
                                          "{{wiki_alias_main_org}}"]
    assert "aliases" not in doc["wikis"][1]


def test_a_new_template_starts_from_the_live_aliases():
    out = _capture([{"id": "main", "name": "Main", "aliases": ["www.example.com", ""]}])
    doc = yaml.safe_load(re.sub(r"\{\{wiki_url_\w+\}\}", "x", out))
    assert doc["wikis"][0]["aliases"] == ["www.example.com"]


def test_the_sync_refuses_live_alias_edits_before_rewriting_anything():
    path = os.path.join(TASKS, "gitops", "tasks", "sync_wikis_yaml.yml")
    with open(path) as f:
        names = [t.get("name") for t in yaml.safe_load(f)]
    refuse = names.index("Refuse alias edits made in config/wikis.yaml")
    assert refuse < names.index("Capture config/wikis.yaml edits into the template")
    assert refuse < names.index("Render template edits into config/wikis.yaml")


def test_the_syncs_template_render_fills_in_aliases_from_the_host_vars():
    out = g.canasta_wikis_render_with_live_urls(TEMPLATE, _live(), VARS)
    assert yaml.safe_load(out)["wikis"][0]["aliases"] == ["www.example.com", ""]
    # Without vars, only urls are filled in, as before.
    assert "{{wiki_alias_main_www}}" in g.canasta_wikis_render_with_live_urls(TEMPLATE, _live())


# --- the wikis.yaml sync, run through Ansible ----------------------------- #

import subprocess  # noqa: E402


def _git(inst, *args):
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=inst, check=True, capture_output=True, text=True).stdout.strip()


def _gitops_repo(tmp_path, live, template=TEMPLATE):
    inst = tmp_path / "inst"
    (inst / "config").mkdir(parents=True)
    (inst / "hosts" / "prod").mkdir(parents=True)
    _git(inst, "init", "-q")
    (inst / "wikis.yaml.template").write_text(template)
    (inst / "hosts" / "prod" / "vars.yaml").write_text(yaml.safe_dump(VARS))
    _git(inst, "add", ".")
    _git(inst, "commit", "-qm", "one")
    (inst / ".gitops-host").write_text("prod\n")
    (inst / ".gitops-applied").write_text(_git(inst, "rev-parse", "HEAD") + "\n")
    (inst / "config" / "wikis.yaml").write_text(live)
    return inst


def _run_sync(tmp_path, inst):
    common = os.path.join(REPO_ROOT, "roles", "common")
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost", "connection": "local", "gather_facts": False,
        "vars": {"instance_path": str(inst), "canasta_root": REPO_ROOT,
                 "ansible_python_interpreter": sys.executable},
        "tasks": [{"ansible.builtin.include_tasks": os.path.join(
            TASKS, "gitops", "tasks", "sync_wikis_yaml.yml")}],
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_FILTER_PLUGINS=os.path.join(REPO_ROOT, "filter_plugins"),
               ANSIBLE_LIBRARY=os.path.join(common, "library"),
               ANSIBLE_MODULE_UTILS=os.path.join(common, "module_utils"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    return subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env, cwd=tmp_path)


RENDERED = ('wikis:\n- id: main\n  url: example.com\n  name: "Main"\n'
            '  aliases:\n    - "www.example.com"\n    - ""\n')


def test_sync_accepts_aliases_rendered_from_the_template(tmp_path):
    inst = _gitops_repo(tmp_path, RENDERED)
    proc = _run_sync(tmp_path, inst)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (inst / "config" / "wikis.yaml").read_text() == RENDERED


def test_sync_refuses_an_alias_edited_in_config_wikis_yaml(tmp_path):
    inst = _gitops_repo(tmp_path, RENDERED.replace('""', '"example.net"'))
    proc = _run_sync(tmp_path, inst)
    assert proc.returncode != 0
    out = " ".join((proc.stdout + proc.stderr).split())
    assert "aliases of wiki main were" in out
    assert "come from wikis.yaml.template" in out


def test_sync_renders_a_template_edit_with_the_host_aliases(tmp_path):
    inst = _gitops_repo(tmp_path, RENDERED)
    (inst / "wikis.yaml.template").write_text(TEMPLATE.replace('"Main"', '"Main Wiki"'))
    proc = _run_sync(tmp_path, inst)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    live = yaml.safe_load((inst / "config" / "wikis.yaml").read_text())
    assert live["wikis"][0]["name"] == "Main Wiki"
    assert live["wikis"][0]["aliases"] == ["www.example.com", ""]


def test_sync_captures_a_live_edit_and_keeps_the_alias_placeholders(tmp_path):
    inst = _gitops_repo(tmp_path, RENDERED.replace('"Main"', '"Main Wiki"'))
    proc = _run_sync(tmp_path, inst)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    template = (inst / "wikis.yaml.template").read_text()
    assert '"Main Wiki"' in template
    assert g.canasta_wikis_template_aliases(template) == {
        "main": ["{{wiki_alias_main_www}}", "{{wiki_alias_main_org}}"]}


def test_sync_records_the_aliases_it_agrees_on(tmp_path):
    inst = _gitops_repo(tmp_path, RENDERED)
    proc = _run_sync(tmp_path, inst)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert json.loads((inst / ".gitops-wikis-record").read_text()) == {
        "aliases": {"main": ["www.example.com"]}}
    # The urls are recorded by the render, after the .env sync compares them.


def test_sync_accepts_a_vars_alias_change_staged_before_it_is_rendered(tmp_path):
    inst = _gitops_repo(tmp_path, RENDERED)
    assert _run_sync(tmp_path, inst).returncode == 0
    # 'canasta config set' stages the vars before the render that applies them.
    (inst / "hosts" / "prod" / "vars.yaml").write_text(
        yaml.safe_dump(dict(VARS, wiki_alias_main_www="www.example.org")))
    _git(inst, "add", "hosts/prod/vars.yaml")
    proc = _run_sync(tmp_path, inst)
    assert proc.returncode == 0, proc.stdout + proc.stderr
