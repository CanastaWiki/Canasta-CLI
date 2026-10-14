"""The Kubernetes web pod connects with MediaWiki's own database account.

With db.wikiUser set to an account other than root, the web Deployment gains
an ensure-db-account init container that creates and grants the account
before MediaWiki starts. Only that init container receives the root
password; the web container gets the account and its WIKI_DB_PASSWORD.
These tests render the chart with `helm template`.
"""

import os
import shutil
import subprocess

import pytest
import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
CHART = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "files", "helm", "canasta")

WIKIS = """wikis:
  - id: main
    url: example.com
    extra_databases:
      - main_cargo
      - name: shared_x
      - main
  - id: second
    url: example.com/second
"""


def _render(tmp_path, values, show_only="templates/deployment-web.yaml"):
    if not shutil.which("helm"):
        pytest.skip("helm is not installed")
    vf = tmp_path / "values.yaml"
    vf.write_text(yaml.safe_dump(values))
    cmd = ["helm", "template", "canasta", CHART, "-f", str(vf)]
    if show_only:
        cmd += ["--show-only", show_only]
    return subprocess.run(cmd, capture_output=True, text=True)


def _web(tmp_path, wiki_user=None, wikis=WIKIS, **extra):
    values = {"instance": {"id": "t1"}, "db": {}}
    if wiki_user is not None:
        values["db"]["wikiUser"] = wiki_user
    if wikis is not None:
        values["configData"] = {"web": {"wikis.yaml": wikis}}
    for key, value in extra.items():
        values[key] = value
    out = _render(tmp_path, values)
    assert out.returncode == 0, out.stderr
    return yaml.safe_load(out.stdout)["spec"]["template"]


def _env(container):
    return {e["name"]: e for e in container.get("env", [])}


def _container(spec, name):
    return next(c for c in spec["containers"] if c["name"] == name)


def _init(spec, name):
    return next((c for c in spec.get("initContainers", [])
                 if c["name"] == name), None)


class TestRootAccount:
    def test_default_renders_no_init_container_and_root(self, tmp_path):
        tpl = _web(tmp_path)
        spec = tpl["spec"]
        assert _init(spec, "ensure-db-account") is None
        env = _env(_container(spec, "web"))
        assert env["MYSQL_USER"]["value"] == "root"
        assert env["MYSQL_PASSWORD"]["valueFrom"]["secretKeyRef"]["key"] == \
            "MYSQL_PASSWORD"
        assert "checksum/db-account" not in tpl["metadata"]["annotations"]
        assert not any(v["name"] == "db-account-script"
                       for v in spec["volumes"])

    def test_no_script_configmap_while_on_root(self, tmp_path):
        out = _render(tmp_path, {"instance": {"id": "t1"}},
                      show_only="templates/configmap-db-account.yaml")
        assert out.returncode != 0
        assert "could not find template" in out.stderr

    def test_external_database_ignores_wiki_user(self, tmp_path):
        values = {"instance": {"id": "t1"},
                  "db": {"enabled": False, "wikiUser": "mediawiki"},
                  "externalDatabase": {"host": "ext", "user": "op"},
                  "configData": {"web": {"wikis.yaml": WIKIS}}}
        out = _render(tmp_path, values)
        assert out.returncode == 0, out.stderr
        spec = yaml.safe_load(out.stdout)["spec"]["template"]["spec"]
        assert _init(spec, "ensure-db-account") is None
        env = _env(_container(spec, "web"))
        assert env["MYSQL_USER"]["value"] == "op"
        assert env["MYSQL_PASSWORD"]["valueFrom"]["secretKeyRef"]["key"] == \
            "MYSQL_PASSWORD"


class TestOwnAccount:
    def test_web_gets_the_account_and_no_root_credential(self, tmp_path):
        spec = _web(tmp_path, "mediawiki")["spec"]
        web = _container(spec, "web")
        env = _env(web)
        assert env["MYSQL_USER"]["value"] == "mediawiki"
        assert env["MYSQL_PASSWORD"]["valueFrom"]["secretKeyRef"]["key"] == \
            "WIKI_DB_PASSWORD"
        assert "MYSQL_ROOT_PASSWORD" not in yaml.safe_dump(web)

    def test_init_container_runs_after_wait_for_db_with_root(self, tmp_path):
        spec = _web(tmp_path, "mediawiki")["spec"]
        names = [c["name"] for c in spec["initContainers"]]
        assert names.index("wait-for-db") < names.index("ensure-db-account")
        init = _init(spec, "ensure-db-account")
        assert init["image"] == "mariadb:11.4"
        assert init["command"] == ["sh", "/scripts/ensure_wiki_db_account.sh"]
        env = _env(init)
        assert env["DB_HOST"]["value"] == "db"
        assert env["WIKI_DB_USER"]["value"] == "mediawiki"
        assert env["WIKI_DB_PASSWORD"]["valueFrom"]["secretKeyRef"] == {
            "name": "t1-db-credentials", "key": "WIKI_DB_PASSWORD"}
        assert env["MYSQL_ROOT_PASSWORD"]["valueFrom"]["secretKeyRef"] == {
            "name": "t1-db-credentials", "key": "MYSQL_ROOT_PASSWORD"}

    def test_database_list_includes_extra_databases(self, tmp_path):
        init = _init(_web(tmp_path, "mediawiki")["spec"], "ensure-db-account")
        assert init["args"] == ["main", "main_cargo", "shared_x", "second"]

    def test_script_is_mounted_from_the_chart(self, tmp_path):
        tpl = _web(tmp_path, "mediawiki")
        spec = tpl["spec"]
        vol = next(v for v in spec["volumes"] if v["name"] == "db-account-script")
        assert vol["configMap"]["name"] == "canasta-t1-db-account"
        assert "checksum/db-account" in tpl["metadata"]["annotations"]
        out = _render(tmp_path, {"instance": {"id": "t1"},
                                 "db": {"wikiUser": "mediawiki"},
                                 "configData": {"web": {"wikis.yaml": WIKIS}}},
                      show_only="templates/configmap-db-account.yaml")
        assert out.returncode == 0, out.stderr
        data = yaml.safe_load(out.stdout)["data"]
        with open(os.path.join(CHART, "files",
                               "ensure_wiki_db_account.sh")) as f:
            assert data["ensure_wiki_db_account.sh"].strip() == f.read().strip()

    def test_a_wiki_set_change_rolls_the_pod(self, tmp_path):
        before = _web(tmp_path, "mediawiki")["metadata"]["annotations"]
        after = _web(tmp_path, "mediawiki", wikis=WIKIS.replace(
            "      - main_cargo\n", ""))["metadata"]["annotations"]
        assert before["checksum/web-config"] != after["checksum/web-config"]

    def test_no_wikis_fails_rather_than_revoking_everything(self, tmp_path):
        if not shutil.which("helm"):
            pytest.skip("helm is not installed")
        out = _render(tmp_path, {"instance": {"id": "t1"},
                                 "db": {"wikiUser": "mediawiki"}})
        assert out.returncode != 0
        assert "no databases to grant" in out.stderr


class TestDbSecret:
    def test_create_writes_the_account_password_beside_root(self):
        path = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks",
                            "k8s_apply_secrets.yml")
        with open(path) as f:
            tasks = yaml.safe_load(f)
        task = next(t for t in tasks
                    if t.get("name") == "Create DB credentials secret")
        data = task["kubernetes.core.k8s"]["definition"]["stringData"]
        assert data == {
            "MYSQL_ROOT_PASSWORD": "{{ _rootdbpass }}",
            "MYSQL_PASSWORD": "{{ _rootdbpass }}",
            "WIKI_DB_PASSWORD": "{{ _wikidbpass }}",
        }
        assert task["no_log"] is True
