"""MW_SITEMAP_URL_PATH and a wiki's sitemap_identifier.

The image's background generator publishes each wiki's sitemaps at
MW_SITEMAP_URL_PATH, relative to the wiki's URL, and names the files after
the wiki's sitemap_identifier in wikis.yaml (default the wiki ID). The CLI
passes the setting to the web container on both orchestrators, serves the
files at that path (Caddy rewrite, edge static class), stores the
identifier, and runs `canasta sitemap generate` with the same arguments.
"""

import os
import subprocess
import sys

import pytest
import yaml

import canasta_edge_render as edge
import canasta_sitemap as rules
import canasta_wikis_yaml as wikis_module
from _orchestrator import INSTANCE_IS_K8S_VARS

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ROLES = os.path.join(REPO_ROOT, "roles")
COMMON = os.path.join(ROLES, "common")
DEFAULTS = os.path.join(ROLES, "config", "defaults", "main.yml")
CLASSIFIER = os.path.join(REPO_ROOT, "vars", "secret_classification.yml")
COMPOSE = os.path.join(ROLES, "orchestrator", "files", "compose", "docker-compose.yml")
K8S_SYNC = os.path.join(ROLES, "orchestrator", "tasks", "k8s_sync_config.yml")
VALIDATE_VALUE = os.path.join(ROLES, "config", "tasks", "_validate_value.yml")
REWRITE = os.path.join(ROLES, "orchestrator", "tasks", "rewrite_caddy.yml")
GENERATE = os.path.join(ROLES, "sitemap", "tasks", "generate.yml")
RECONCILE = os.path.join(ROLES, "gitops", "tasks", "_reconcile_wikis_template.yml")
SET_SINGLE = os.path.join(ROLES, "config", "tasks", "_set_single.yml")
ADD = os.path.join(REPO_ROOT, "playbooks", "add.yml")
KEY = "MW_SITEMAP_URL_PATH"

FARM = [{"id": "main", "url": "example.com", "name": "Main"},
        {"id": "en", "url": "example.com/en", "name": "English"},
        {"id": "docs", "url": "docs.example.org:8443", "name": "Docs"}]
# A root wiki and a wiki at a path on the same host.
PATH_FARM = [{"id": "main", "url": "example.com", "name": "Main"},
             {"id": "docs", "url": "example.com/docs", "name": "Docs"}]


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _env():
    env = {k: v for k, v in os.environ.items() if not k.startswith("ANSIBLE_")}
    env.update(ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_LIBRARY=os.path.join(COMMON, "library"),
               ANSIBLE_MODULE_UTILS=os.path.join(COMMON, "module_utils"),
               ANSIBLE_FILTER_PLUGINS=os.path.join(REPO_ROOT, "filter_plugins"))
    return env


def _play(tmp_path, tasks, play_vars, roles_path=None):
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": dict(INSTANCE_IS_K8S_VARS, ansible_python_interpreter=sys.executable,
                     **play_vars),
        "tasks": tasks,
    }]))
    env = _env()
    env["ANSIBLE_HOME"] = str(tmp_path / "ansible-home")
    if roles_path:
        env["ANSIBLE_ROLES_PATH"] = roles_path
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env, cwd=tmp_path)
    return proc.returncode, proc.stdout + proc.stderr


# --- the rules ------------------------------------------------------------- #

class TestUrlPathRule:
    @pytest.mark.parametrize("value", [
        "/wiki/sitemap", "/sitemap", "/a/b.c/d-e_f~g", "/public_assets/sitemap",
        "/" + "a" * 99])
    def test_accepted(self, value):
        assert rules.url_path_error(value) is None

    @pytest.mark.parametrize("value", [
        "", "wiki/sitemap", "/", "/wiki/sitemap/", "//sitemap", "/wiki//sitemap",
        "/wiki/../x", "/a..b", "/.hidden", "/x/./y", "/wiki sitemap", "/wiki/{x}",
        "/wiki%2Fsitemap", "/wiki/sitemap\n", "/" + "a" * 100])
    def test_refused(self, value):
        assert "must be a URL path" in rules.url_path_error(value)

    @pytest.mark.parametrize("value", [
        "/w", "/w/sitemap", "/public_assets", "/public_assets/x", "/wiki"])
    def test_paths_mediawiki_uses_are_refused(self, value):
        assert "cannot be" in rules.url_path_error(value)


class TestIdentifierRule:
    @pytest.mark.parametrize("value", ["mediawiki", "wiki_en", "a-b.c", "9x"])
    def test_accepted(self, value):
        assert rules.identifier_error(value) is None

    # The image skips a wiki whose identifier matches none of these.
    @pytest.mark.parametrize("value", ["", ".hidden", "-x", "_x", "a/b", "../x",
                                       "a b", "x\n"])
    def test_refused(self, value):
        assert "may contain only" in rules.identifier_error(value)

    def test_defaults_to_the_wiki_id(self):
        assert rules.wiki_identifier({"id": "en"}) == "en"
        assert rules.wiki_identifier({"id": "en", "sitemap_identifier": "mw"}) == "mw"


class TestRewrites:
    @pytest.mark.parametrize("value", [None, "", "/public_assets/sitemap"])
    def test_none_by_default(self, value):
        assert rules.rewrites(FARM, value) == []

    def test_each_wiki_maps_its_path_onto_the_images(self):
        assert rules.rewrites(FARM, "/wiki/sitemap") == [
            {"host": "example.com", "path": "/en/wiki/sitemap",
             "pattern": "^/en/wiki/sitemap/", "target": "/en/public_assets/sitemap"},
            {"host": "docs.example.org", "path": "/wiki/sitemap",
             "pattern": "^/wiki/sitemap/", "target": "/public_assets/sitemap"},
            {"host": "example.com", "path": "/wiki/sitemap",
             "pattern": "^/wiki/sitemap/", "target": "/public_assets/sitemap"},
        ]


# --- the setting ----------------------------------------------------------- #

class TestFarmCollision:
    def test_a_root_wikis_sitemaps_inside_a_path_wiki_are_refused(self):
        assert rules.farm_collision_error(PATH_FARM, "/docs/sitemap") == (
            "MW_SITEMAP_URL_PATH=/docs/sitemap would publish the sitemaps of "
            "wiki 'main' at example.com/docs/sitemap, inside the URL of wiki "
            "'docs' (example.com/docs).")

    def test_the_path_itself_is_refused(self):
        assert "inside the URL of wiki 'docs'" in rules.farm_collision_error(
            PATH_FARM, "/docs")

    def test_a_wiki_under_another_wikis_sitemaps_is_refused(self):
        wikis = [{"id": "main", "url": "example.com"},
                 {"id": "x", "url": "example.com/wiki/sitemap/x"}]
        assert rules.farm_collision_error(wikis, "/wiki/sitemap") == (
            "MW_SITEMAP_URL_PATH=/wiki/sitemap would publish the sitemaps of "
            "wiki 'main' at example.com/wiki/sitemap, which contains the URL "
            "of wiki 'x' (example.com/wiki/sitemap/x).")

    @pytest.mark.parametrize("url_path", ["/docs2/sitemap", "/doc", "/Docs/sitemap",
                                          "/wiki/sitemap"])
    def test_whole_segments_are_compared_case_sensitively(self, url_path):
        assert rules.farm_collision_error(PATH_FARM, url_path) is None

    def test_other_hosts_do_not_collide(self):
        wikis = [{"id": "main", "url": "example.com"},
                 {"id": "docs", "url": "other.example.org/docs"}]
        assert rules.farm_collision_error(wikis, "/docs/sitemap") is None

    def test_hosts_are_compared_without_case_or_port(self):
        wikis = [{"id": "main", "url": "Example.com:8443"},
                 {"id": "docs", "url": "example.com/docs"}]
        assert "wiki 'main' at example.com/docs/sitemap" in (
            rules.farm_collision_error(wikis, "/docs/sitemap"))

    def test_a_path_wikis_own_sitemaps_are_fine(self):
        assert rules.farm_collision_error(PATH_FARM[1:], "/docs/sitemap") is None
        assert rules.farm_collision_error(FARM, "/wiki/sitemap") is None

    def test_the_rewrites_leave_out_only_the_overlapping_wiki(self):
        wikis = PATH_FARM + [{"id": "o", "url": "other.example.org"}]
        assert [r["path"] for r in rules.rewrites(wikis, "/docs/sitemap")] == [
            "/docs/docs/sitemap", "/docs/sitemap"]
        assert [r["host"] for r in rules.rewrites(wikis, "/docs/sitemap")] == [
            "example.com", "other.example.org"]

    def test_a_warning_names_both_wikis(self):
        wikis = [{"id": "main", "url": "example.com"},
                 {"id": "x", "url": "example.com/wiki/sitemap/x"}]
        assert rules.farm_collision_warnings(wikis, "/wiki/sitemap") == [
            "MW_SITEMAP_URL_PATH=/wiki/sitemap: the sitemaps of wiki 'main' are not "
            "served at example.com/wiki/sitemap, which contains the URL of wiki 'x' "
            "(example.com/wiki/sitemap/x)."]
        assert rules.farm_collision_warnings(PATH_FARM, "/wiki/sitemap") == []

    @pytest.mark.parametrize("url_path", ["", None, "/public_assets/sitemap"])
    def test_none_by_default(self, url_path):
        wikis = PATH_FARM + [{"id": "p", "url": "example.com/public_assets"}]
        assert rules.farm_collision_error(wikis, url_path) is None


class TestSetting:
    def test_key_is_known_and_documented(self):
        keys = {k["name"]: k for k in _load(DEFAULTS)["canasta_known_keys"]}
        assert keys[KEY]["group"] == "Sitemaps"
        assert keys[KEY]["default"] == "/public_assets/sitemap"

    def test_key_is_shared_and_not_secret_in_gitops(self):
        cls = _load(CLASSIFIER)
        assert KEY not in cls["canasta_host_specific_nonsecret"]
        assert KEY not in cls["canasta_secret_explicit"]

    def test_compose_passes_it_with_the_images_default(self):
        env = _load(COMPOSE)["services"]["web"]["environment"]
        assert "MW_SITEMAP_URL_PATH=${MW_SITEMAP_URL_PATH:-/public_assets/sitemap}" in env

    def test_kubernetes_passes_it_to_the_web_pods(self):
        allow = next(t["ansible.builtin.set_fact"]["_k8s_pod_env_allowlist"]
                     for t in _load(K8S_SYNC)
                     if "_k8s_pod_env_allowlist" in t.get("ansible.builtin.set_fact", {}))
        assert KEY in allow

    def _validate(self, tmp_path, value):
        task = next(t for t in _load(VALIDATE_VALUE)
                    if t.get("name") == "Validate MW_SITEMAP_URL_PATH")
        return _play(tmp_path, [task], {"_config_key": KEY, "_config_value": value})

    @pytest.mark.parametrize("value", ["/wiki/sitemap", "/public_assets/sitemap", ""])
    def test_config_set_accepts(self, tmp_path, value):
        code, out = self._validate(tmp_path, value)
        assert code == 0, out

    @pytest.mark.parametrize("value", ["wiki/sitemap", "/wiki/sitemap/", "/a/../b", "/w"])
    def test_config_set_refuses(self, tmp_path, value):
        code, out = self._validate(tmp_path, value)
        assert code != 0 and "MW_SITEMAP_URL_PATH" in out, out


def _config_set(tmp_path, value, wikis=PATH_FARM):
    inst = tmp_path / "inst"
    (inst / "config").mkdir(parents=True)
    (inst / ".env").write_text("MW_SITE_SERVER=https://example.com\n")
    (inst / "config" / "wikis.yaml").write_text(yaml.safe_dump({"wikis": wikis}))
    play_vars = dict(_load(DEFAULTS), **_load(CLASSIFIER))
    play_vars.update(instance_path=str(inst), canasta_root=REPO_ROOT,
                     instance_orchestrator="compose",
                     _setting="%s=%s" % (KEY, value))
    code, out = _play(tmp_path, [{"ansible.builtin.include_tasks": SET_SINGLE}],
                      play_vars)
    return code, out, (inst / ".env").read_text()


class TestConfigSetFarm:
    def test_refuses_before_writing_env(self, tmp_path):
        code, out, env = _config_set(tmp_path, "/docs/sitemap")
        assert code != 0, out
        assert "would publish the sitemaps of wiki 'main'" in out, out
        assert env == "MW_SITE_SERVER=https://example.com\n"

    def test_accepts_a_path_that_overlaps_no_wiki(self, tmp_path):
        code, out, env = _config_set(tmp_path, "/wiki/sitemap")
        assert code == 0, out
        assert "MW_SITEMAP_URL_PATH=/wiki/sitemap" in env


ADD_TASKS = ("Read the instance's ports from .env", "Parse URL into domain and path",
             "Read the wikis to check the sitemap paths",
             "Fail if the new wiki would overlap another wiki's sitemaps")


def _add(tmp_path, url, env_lines, wikis):
    inst = tmp_path / "inst"
    (inst / "config").mkdir(parents=True)
    (inst / ".env").write_text("".join(line + "\n" for line in env_lines))
    (inst / "config" / "wikis.yaml").write_text(yaml.safe_dump({"wikis": wikis}))
    found = {t.get("name"): t for t in _load(ADD)}
    return _play(tmp_path, [found[n] for n in ADD_TASKS],
                 {"instance_path": str(inst), "url": url, "wiki": "new"})


class TestAddFarm:
    def test_a_path_wiki_over_an_existing_sitemap_path_is_refused(self, tmp_path):
        code, out = _add(tmp_path, "example.com/docs", ["MW_SITEMAP_URL_PATH=/docs/sitemap"],
                         PATH_FARM[:1])
        assert code != 0, out
        assert ("Cannot add wiki 'new': MW_SITEMAP_URL_PATH=/docs/sitemap would "
                "publish the sitemaps of wiki 'main'") in out, out

    def test_a_wiki_whose_sitemaps_land_in_another_wiki_is_refused(self, tmp_path):
        wikis = [{"id": "main", "url": "example.com/docs", "name": "Main"}]
        code, out = _add(tmp_path, "example.com", ["MW_SITEMAP_URL_PATH=/docs/sitemap"], wikis)
        assert code != 0, out
        assert ("would publish the sitemaps of wiki 'new' at example.com/docs/sitemap, "
                "inside the URL of wiki 'main'") in out, out

    @pytest.mark.parametrize("env_lines,url", [
        ([], "example.com/docs"),
        (["MW_SITEMAP_URL_PATH="], "example.com/docs"),
        (["MW_SITEMAP_URL_PATH=/docs/sitemap"], "example.com/docs2"),
        (["MW_SITEMAP_URL_PATH=/docs/sitemap"], "other.example.org/docs"),
    ])
    def test_other_wikis_are_added(self, tmp_path, env_lines, url):
        code, out = _add(tmp_path, url, env_lines, PATH_FARM[:1])
        assert code == 0, out


# --- serving: Caddy -------------------------------------------------------- #

def _caddyfile(tmp_path, env_lines, wikis=FARM, k8s=False):
    inst = tmp_path / "inst"
    (inst / "config").mkdir(parents=True)
    (inst / ".env").write_text("".join(line + "\n" for line in env_lines))
    (inst / "config" / "wikis.yaml").write_text(yaml.safe_dump({"wikis": wikis}))
    play_vars = dict(instance_path=str(inst), canasta_root=REPO_ROOT,
                     instance_orchestrator="kubernetes" if k8s else "compose")
    if k8s:
        play_vars["instance_is_k8s"] = True
    code, out = _play(tmp_path, [{"ansible.builtin.include_tasks": REWRITE}], play_vars)
    return code, out, inst / "config" / "Caddyfile"


class TestCaddy:
    def test_default_render_is_unchanged(self, tmp_path):
        renders = []
        for n, lines in enumerate([[], ["MW_SITEMAP_URL_PATH="],
                                   ["MW_SITEMAP_URL_PATH=/public_assets/sitemap"]]):
            code, out, path = _caddyfile(tmp_path / str(n), lines)
            assert code == 0, out
            renders.append(path.read_text())
        assert renders[0] == renders[1] == renders[2]
        assert "sitemap" not in renders[0] and "uri " not in renders[0]

    @pytest.mark.parametrize("k8s", [False, True])
    def test_each_wiki_is_rewritten_onto_the_images_path(self, tmp_path, k8s):
        code, out, path = _caddyfile(tmp_path, ["MW_SITEMAP_URL_PATH=/wiki/sitemap"], k8s=k8s)
        assert code == 0, out
        text = path.read_text()
        assert ("    @sitemap1 {\n"
                "        host example.com\n"
                "        path /en/wiki/sitemap/*\n"
                "    }\n"
                "    uri @sitemap1 path_regexp ^/en/wiki/sitemap/ /en/public_assets/sitemap/\n"
                ) in text
        assert ("        host docs.example.org\n"
                "        path /wiki/sitemap/*\n") in text
        assert "uri @sitemap3 path_regexp ^/wiki/sitemap/ /public_assets/sitemap/\n" in text

    def test_regex_characters_in_the_path_are_escaped(self, tmp_path):
        code, out, path = _caddyfile(tmp_path, ["MW_SITEMAP_URL_PATH=/site.map"],
                                     wikis=[FARM[0]])
        assert code == 0, out
        assert "path_regexp ^/site\\.map/ /public_assets/sitemap/" in path.read_text()

    def test_an_unusable_path_is_refused(self, tmp_path):
        code, out, path = _caddyfile(tmp_path, ["MW_SITEMAP_URL_PATH=/wiki/sitemap/"])
        assert code != 0 and "MW_SITEMAP_URL_PATH must be a URL path" in out, out
        assert not path.exists()

    @pytest.mark.parametrize("k8s", [False, True])
    def test_an_overlapping_wikis_rewrite_is_left_out(self, tmp_path, k8s):
        code, out, path = _caddyfile(tmp_path, ["MW_SITEMAP_URL_PATH=/docs/sitemap"],
                                     wikis=PATH_FARM, k8s=k8s)
        assert code == 0, out
        text = path.read_text()
        assert "path /docs/docs/sitemap/*" in text
        assert "path /docs/sitemap/*" not in text
        warning = ("Warning: MW_SITEMAP_URL_PATH=/docs/sitemap: the sitemaps of wiki "
                   "'main' are not served at example.com/docs/sitemap, which is inside "
                   "the URL of wiki 'docs' (example.com/docs).")
        # On Compose the edge render reports it.
        assert (warning in out) == k8s, out


# --- serving: the HAProxy edge -------------------------------------------- #

def _edge_inst(instance_id, env, wikis=FARM[:2]):
    return edge.instance_from_config(instance_id, env, wikis)


class TestEdge:
    def test_default_render_is_unchanged(self):
        base = edge.render([_edge_inst("a", {})])
        for value in ("", "/public_assets/sitemap"):
            assert edge.render([_edge_inst("a", {KEY: value})]) == base
        assert "sitemap" not in base["files"]["haproxy.cfg"]

    def test_the_sitemap_paths_are_static_files_of_their_instance(self):
        cfg = edge.render([
            _edge_inst("a", {KEY: "/wiki/sitemap"}),
            _edge_inst("b", {}, [{"id": "x", "url": "other.example.org"}]),
        ])["files"]["haproxy.cfg"]
        frontend = cfg.split("frontend https\n", 1)[1].split("\n\n", 1)[0]
        lines = frontend.splitlines()
        rule = ("    http-request set-var(txn.sitemap) bool(true) if "
                "{ var(txn.inst) -m str a } { path_beg /en/wiki/sitemap/ /wiki/sitemap/ }")
        assert rule in lines
        assert "-m str b } { path_beg" not in frontend
        acl = "    acl static_file var(txn.sitemap) -m bool"
        static = "    http-request set-var(txn.static) bool(true) if static_file !php"
        assert lines.index(rule) < lines.index(acl) < lines.index(static)

    def test_a_plain_http_instance_classifies_them_too(self):
        cfg = edge.render([_edge_inst("a", {KEY: "/wiki/sitemap", "TLS_MODE": "off"})])
        frontend = cfg["files"]["haproxy.cfg"].split("frontend http\n", 1)[1]
        assert "path_beg /en/wiki/sitemap/ /wiki/sitemap/" in frontend

    def test_an_unusable_path_is_refused(self):
        with pytest.raises(edge.EdgeError, match="Instance 'a': MW_SITEMAP_URL_PATH"):
            _edge_inst("a", {KEY: "/wiki/sitemap/"})

    def test_an_overlapping_wikis_path_is_left_out_and_reported(self):
        inst = _edge_inst("a", {KEY: "/docs/sitemap"}, PATH_FARM)
        assert inst["sitemap_paths"] == ["/docs/docs/sitemap/"]
        assert inst["sitemap_overlaps"] == [
            "MW_SITEMAP_URL_PATH=/docs/sitemap: the sitemaps of wiki 'main' are not "
            "served at example.com/docs/sitemap, which is inside the URL of wiki "
            "'docs' (example.com/docs)."]
        assert _edge_inst("a", {KEY: "/wiki/sitemap"}, PATH_FARM)["sitemap_overlaps"] == []


# --- the identifier in wikis.yaml ----------------------------------------- #

class _Module:
    def __init__(self, **params):
        self.params = dict(params)
        self.check_mode = False
        self.failed = None

    def fail_json(self, msg):
        self.failed = msg


class TestWikisYaml:
    def _instance(self, tmp_path, wikis=FARM):
        (tmp_path / "config").mkdir()
        (tmp_path / "config" / "wikis.yaml").write_text(yaml.safe_dump({"wikis": wikis}))
        return str(tmp_path)

    def _set(self, path, wiki_id, identifier):
        module = _Module(wiki_id=wiki_id, sitemap_identifier=identifier)
        result = wikis_module._state_sitemap_identifier(module, path, "set_sitemap_identifier")
        return module, result

    def test_sets_and_restores_the_default(self, tmp_path):
        path = self._instance(tmp_path)
        _, result = self._set(path, "en", "mediawiki")
        assert result["changed"]
        assert wikis_module.read_wikis(path)[1] == dict(FARM[1], sitemap_identifier="mediawiki")
        _, result = self._set(path, "en", "mediawiki")
        assert not result["changed"]
        _, result = self._set(path, "en", "en")
        assert result["changed"]
        assert wikis_module.read_wikis(path)[1] == FARM[1]

    def test_refuses_an_unusable_identifier(self, tmp_path):
        path = self._instance(tmp_path)
        module, result = self._set(path, "en", "../x")
        assert result is None and "may contain only" in module.failed
        assert wikis_module.read_wikis(path) == FARM

    def test_refuses_an_unknown_wiki(self, tmp_path):
        module, result = self._set(self._instance(tmp_path), "fr", "mediawiki")
        assert result is None and "Wiki ID 'fr' not found" in module.failed

    def test_the_gitops_template_keeps_it(self, tmp_path):
        inst = tmp_path / "inst"
        inst.mkdir()
        self._instance(inst, [FARM[0], dict(FARM[1], sitemap_identifier="mediawiki")])
        code, out = _play(tmp_path, [{"ansible.builtin.include_tasks": RECONCILE}],
                          {"instance_path": str(inst), "reconcile_create": True})
        assert code == 0, out
        template = (inst / "wikis.yaml.template").read_text()
        assert yaml.safe_load(template.replace("{{", "").replace("}}", ""))["wikis"][1][
            "sitemap_identifier"] == "mediawiki"
        assert "sitemap_identifier" not in template.split("- id: en")[0]


# --- canasta sitemap generate --------------------------------------------- #

STUB_EXEC = """\
- name: Record the exec
  ansible.builtin.lineinfile:
    path: "{{ instance_path }}/exec.log"
    line: "{{ exec_command }}"
    create: true
- name: Stub exec result
  ansible.builtin.set_fact:
    exec_result: {stdout: "", rc: 0}
"""


def _generate(tmp_path, extra_vars, wikis=FARM, k8s=False, gitops=False):
    root = tmp_path / "root"
    (root / "roles" / "common" / "tasks").mkdir(parents=True)
    (root / "roles" / "common" / "tasks" / "resolve_instance.yml").write_text(
        "- ansible.builtin.set_fact:\n    instance_id: demo\n")
    orch = root / "roles" / "orchestrator" / "tasks"
    orch.mkdir(parents=True)
    (orch / "exec.yml").write_text(STUB_EXEC)
    for name in ("apply_config", "check_running"):
        (orch / ("%s.yml" % name)).write_text(
            "- ansible.builtin.lineinfile:\n"
            "    path: \"{{ instance_path }}/exec.log\"\n"
            "    line: %s\n    create: true\n" % name)
    gitops_tasks = root / "roles" / "gitops" / "tasks"
    gitops_tasks.mkdir(parents=True)
    (gitops_tasks / "capture_wikis_yaml.yml").write_text(
        "- ansible.builtin.lineinfile:\n"
        "    path: \"{{ instance_path }}/exec.log\"\n"
        "    line: captured\n    create: true\n")
    inst = tmp_path / "inst"
    (inst / "config").mkdir(parents=True)
    (inst / "config" / "wikis.yaml").write_text(yaml.safe_dump({"wikis": wikis}))
    (inst / ".env").write_text("MW_SITE_SERVER=https://example.com\n")
    if gitops:
        (inst / ".gitops-host").write_text("")
    play_vars = dict(instance_path=str(inst), canasta_root=str(root),
                     instance_orchestrator="kubernetes" if k8s else "compose",
                     **extra_vars)
    if k8s:
        play_vars["instance_is_k8s"] = True
    code, out = _play(tmp_path, [{"ansible.builtin.include_tasks": GENERATE}],
                      play_vars, roles_path=str(root / "roles"))
    log = inst / "exec.log"
    return code, out, inst, (log.read_text().splitlines() if log.exists() else [])


def _php_call(log, wiki):
    return next(line for line in log if "generateSitemap.php" in line
                and "--wiki=%s " % wiki in line)


class TestGenerate:
    def test_passes_the_background_generators_arguments(self, tmp_path):
        code, out, _, log = _generate(tmp_path, {})
        assert code == 0, out
        call = _php_call(log, "en")
        assert '--urlpath="${MW_SITEMAP_URL_PATH:-/public_assets/sitemap}"' in call
        assert "--server=https://example.com/en " in call
        assert "--fspath=/mediawiki/public_assets/en/sitemap " in call
        assert "--identifier=en " in call
        assert "--identifier=main " in _php_call(log, "main")
        assert "apply_config" not in log and "captured" not in log

    def test_removes_the_files_of_other_identifiers_after_success(self, tmp_path):
        wikis = [dict(FARM[1], sitemap_identifier="mediawiki")]
        code, out, _, log = _generate(tmp_path, {}, wikis=wikis)
        assert code == 0, out
        call = _php_call(log, "en")
        assert "--identifier=mediawiki && for f in /mediawiki/public_assets/en/sitemap/sitemap-*;" in call
        assert ("sitemap-index-mediawiki.xml | sitemap-mediawiki-NS_*) ;; "
                "sitemap-index-*.xml | sitemap-*-NS_*) rm -f -- \"$f\" ;;") in call

    def test_the_cleanup_keeps_the_current_identifiers_files(self, tmp_path):
        wikis = [dict(FARM[1], sitemap_identifier="mediawiki")]
        _, _, _, log = _generate(tmp_path, {}, wikis=wikis)
        call = _php_call(log, "en")
        cleanup = call.split(" && ", 1)[1].replace(
            "/mediawiki/public_assets/en/sitemap", str(tmp_path / "sm"))
        (tmp_path / "sm").mkdir()
        for name in ("sitemap-index-mediawiki.xml", "sitemap-mediawiki-NS_0-0.xml.gz",
                     "sitemap-index-en.xml", "sitemap-en-NS_0-0.xml.gz",
                     "sitemap-index-mediawiki-old.xml", "notes.txt"):
            (tmp_path / "sm" / name).touch()
        subprocess.run(["bash", "-c", cleanup], check=True)
        assert sorted(os.listdir(tmp_path / "sm")) == [
            "notes.txt", "sitemap-index-mediawiki.xml", "sitemap-mediawiki-NS_0-0.xml.gz"]

    def test_identifier_is_stored_applied_and_used(self, tmp_path):
        code, out, inst, log = _generate(tmp_path, {"wiki": "en", "identifier": "mediawiki"})
        assert code == 0, out
        stored = yaml.safe_load((inst / "config" / "wikis.yaml").read_text())["wikis"]
        assert stored[1]["sitemap_identifier"] == "mediawiki"
        assert "--identifier=mediawiki " in _php_call(log, "en")
        assert log.index("captured") < log.index("apply_config") < log.index(_php_call(log, "en"))

    def test_identifier_needs_a_wiki(self, tmp_path):
        code, out, _, log = _generate(tmp_path, {"identifier": "mediawiki"})
        assert code != 0 and "pass it together with --wiki" in out, out
        assert log == []

    def test_an_unusable_identifier_is_refused(self, tmp_path):
        code, out, inst, log = _generate(tmp_path, {"wiki": "en", "identifier": ".x"})
        assert code != 0 and "may contain only" in out, out
        assert log == []
        assert "sitemap_identifier" not in (inst / "config" / "wikis.yaml").read_text()

    def test_an_unusable_identifier_in_wikis_yaml_is_refused(self, tmp_path):
        wikis = [FARM[0], dict(FARM[1], sitemap_identifier="a/b")]
        code, out, _, log = _generate(tmp_path, {}, wikis=wikis)
        assert code != 0 and "gives wiki 'en' an unusable sitemap_identifier" in out, out
        assert log == []

    def test_a_gitops_kubernetes_instance_only_stages_the_identifier(self, tmp_path):
        code, out, _, log = _generate(tmp_path, {"wiki": "en", "identifier": "mediawiki"},
                                      k8s=True, gitops=True)
        assert code == 0, out
        assert log == ["captured", "check_running"]
        assert "staged in its gitops repo but not deployed" in out
