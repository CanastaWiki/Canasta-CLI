"""The canasta_opensearch module: membership, the cluster's lifecycle, the
instances' users and files.

A fake container runner stands in for docker/podman and for the cluster's
REST API.
"""

import json
import os

import pytest
import yaml

import canasta_opensearch as mod
import canasta_opensearch_render as render


class FakeCluster:
    """Records argv; keeps the containers, the security index and the
    indexes the commands would change."""

    def __init__(self, counts=None, users=None):
        self.calls = []
        self.stdin = []
        self.running = False
        self.secured = False
        self.volume = False
        # {index name: document count}
        self.counts = dict(counts or {})
        self.users = dict(users or {})
        self.roles = {}
        self.mappings = {}
        self.deleted_indexes = []
        self.certificate_runs = 0

    def __call__(self, argv, env=None, cwd=None, data=None):
        self.calls.append(list(argv))
        if data is not None:
            self.stdin.append(data)
        if argv[0] == "docker" and argv[1] == "compose":
            if "up" in argv:
                self.running = True
                self.volume = True
            elif "down" in argv:
                self.running = False
            return 0, "", ""
        verb = argv[1]
        if verb == "info":
            return 0, "daemon\n", ""
        if verb in ("image", "pull"):
            return 0, "", ""
        if verb == "volume":
            return (0 if self.volume else 1), "", ""
        if verb == "ps":
            return 0, ("abc\n" if self.running else ""), ""
        if verb == "run":
            self._run(argv)
            return 0, "", ""
        if verb == "exec":
            if "curl" in argv:
                return self._curl(argv, data)
            self.secured = True
            return 0, "Done with success\n", ""
        raise AssertionError("unexpected command %r" % argv)

    def _run(self, argv):
        self.volume = self.volume or any(a.startswith(render.CONF_VOLUME + ":") for a in argv)
        script = argv[-1]
        if "openssl" not in script:
            return
        out = next(a.split(":")[0] for a in argv if a.endswith(":/out"))
        self.certificate_runs += 1
        names = list(mod.CERT_FILES) + (["ca.key"] if "/out/ca.key" in script else [])
        for name in names:
            with open(os.path.join(out, name), "w") as handle:
                handle.write("%s %d\n" % (name, self.certificate_runs))

    def _curl(self, argv, data):
        method = argv[argv.index("-X") + 1]
        url = next(a for a in argv if a.startswith("https://"))
        path = url[len("https://localhost:9200"):]
        body = json.loads(data) if data else None
        code, reply = self._api(method, path, body)
        return 0, "%s\n%d" % (json.dumps(reply), code), ""

    def _api(self, method, path, body):
        if not self.running:
            return 0, None
        if path == "/_plugins/_security/authinfo":
            return (200, {"user": "admin"}) if self.secured else (503, "not initialized")
        security = "/_plugins/_security/api/"
        if path.startswith(security):
            kind, _, name = path[len(security):].partition("/")
            store = {"internalusers": self.users, "roles": self.roles,
                     "rolesmapping": self.mappings}[kind]
            if method == "PUT":
                store[name] = body
                return 200, {"status": "OK"}
            if method == "DELETE":
                return (200, {}) if store.pop(name, None) is not None else (404, {})
            return 200, dict(store)
        if path.startswith("/_cat/indices/"):
            prefix = path[len("/_cat/indices/"):].split("*")[0]
            return 200, [{"index": i} for i in sorted(self.counts) if i.startswith(prefix)]
        if path.endswith("/_count"):
            name = path[1:-len("/_count")]
            if name not in self.counts:
                return 404, {"error": "index_not_found_exception"}
            return 200, {"count": self.counts[name]}
        if method == "DELETE":
            for name in path[1:].split(","):
                self.counts.pop(name, None)
                self.deleted_indexes.append(name)
            return 200, {"acknowledged": True}
        raise AssertionError("unexpected API call %s %s" % (method, path))

    def compose_calls(self):
        return [c for c in self.calls if c[:2] == ["docker", "compose"]]


def _instance(root, name, token="tok%s", password="pw-%s", heap=None,
              wikis=("main",), enabled=True):
    path = root / name
    (path / "config").mkdir(parents=True)
    env = {"CANASTA_ENABLE_OPENSEARCH": "true" if enabled else "false"}
    if token:
        env["CANASTA_OPENSEARCH_TOKEN"] = token % name if "%s" in token else token
    if password:
        env["CANASTA_OPENSEARCH_PASSWORD"] = password % name
    if heap:
        env["CANASTA_OPENSEARCH_HEAP"] = heap
    (path / ".env").write_text("".join("%s=%s\n" % kv for kv in env.items()))
    (path / "config" / "wikis.yaml").write_text(yaml.safe_dump(
        {"wikis": [{"id": w, "url": "%s.example.com" % w} for w in wikis]}))
    return str(path)


def _set_env(path, **values):
    env = mod.read_env(path)
    env.update(values)
    with open(os.path.join(path, ".env"), "w") as handle:
        handle.write("".join("%s=%s\n" % kv for kv in env.items()))


def _sync(run, os_dir, inst_id, path, member="present"):
    return mod.sync(run, "docker", "docker compose", str(os_dir), inst_id, path, member)


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)


def _read(path, *parts):
    with open(os.path.join(path, *parts)) as handle:
        return handle.read()


# --- joining ------------------------------------------------------------- #

def test_joining_starts_the_cluster_and_sets_up_the_instance(tmp_path):
    run, os_dir = FakeCluster(), tmp_path / "opensearch"
    a = _instance(tmp_path, "a")
    result = _sync(run, os_dir, "a", a)

    assert result["running"] and result["changed"]
    assert result["members"] == ["a"]
    assert result["heap_mb"] == render.DEFAULT_HEAP_MB
    assert not result.get("restarted")
    assert run.secured
    assert run.users["toka"]["password"] == "pw-a"
    assert run.users["toka"]["attributes"] == {mod.INSTANCE_ATTRIBUTE: "a"}
    assert run.roles["toka"]["index_permissions"][0]["index_patterns"] == ["toka_*"]
    assert run.mappings["toka"]["users"] == ["toka"]
    # The cluster directory is private, and holds the CA key.
    assert oct(os.stat(os_dir).st_mode & 0o777) == "0o700"
    assert os.path.exists(os_dir / "ca.key")
    assert yaml.safe_load(_read(os_dir, "members.yaml")) == {
        "members": [{"id": "a", "path": a}], "removed": []}
    # The instance's files.
    assert os.path.exists(os.path.join(a, render.LAYER_FILE))
    assert "'username' => 'toka'" in _read(a, render.GLOBAL_SETTINGS)
    assert result["wikis"] == ["main"] and result["indexed"] == []
    assert "$wgSearchType" not in _read(a, render.WIKI_SETTINGS % "main")


def test_passwords_go_on_stdin_never_in_argv(tmp_path):
    run = FakeCluster()
    _sync(run, tmp_path / "opensearch", "a", _instance(tmp_path, "a"))
    assert not any("pw-a" in arg for call in run.calls for arg in call)
    assert any("pw-a" in data for data in run.stdin)


def test_an_indexed_wiki_searches_with_cirrussearch(tmp_path):
    run = FakeCluster(counts={"toka_main_content": 12})
    a = _instance(tmp_path, "a", wikis=("main", "docs"))
    result = _sync(run, tmp_path / "opensearch", "a", a)
    assert result["indexed"] == ["main"]
    assert "$wgSearchType = 'CirrusSearch';" in _read(a, render.WIKI_SETTINGS % "main")
    assert "$wgSearchType" not in _read(a, render.WIKI_SETTINGS % "docs")


def test_an_indexed_wiki_keeps_cirrussearch_when_the_cluster_cannot_be_reached(tmp_path):
    run = FakeCluster(counts={"toka_main_content": 3})
    os_dir = tmp_path / "opensearch"
    a = _instance(tmp_path, "a")
    _sync(run, os_dir, "a", a)

    def unreachable(argv, env=None, cwd=None, data=None):
        if argv[:2] == ["docker", "compose"]:
            return 1, "", "daemon gone"
        return run(argv, env, cwd, data)

    run.running = False
    with pytest.raises(mod.OpenSearchError):
        _sync(unreachable, os_dir, "a", a)
    assert "$wgSearchType = 'CirrusSearch';" in _read(a, render.WIKI_SETTINGS % "main")


def test_a_second_sync_with_nothing_changed_restarts_nothing(tmp_path):
    run, os_dir = FakeCluster(), tmp_path / "opensearch"
    a = _instance(tmp_path, "a")
    _sync(run, os_dir, "a", a)
    ups = len(run.compose_calls())
    result = _sync(run, os_dir, "a", a)
    assert len(run.compose_calls()) == ups
    assert not result.get("restarted")
    assert not result["changed"]


def test_the_largest_member_heap_restarts_the_cluster(tmp_path):
    run, os_dir = FakeCluster(), tmp_path / "opensearch"
    a = _instance(tmp_path, "a", heap="512m")
    b = _instance(tmp_path, "b", heap="2g")
    assert _sync(run, os_dir, "a", a)["heap_mb"] == 512
    result = _sync(run, os_dir, "b", b)
    assert result["heap_mb"] == 2048
    assert result["restarted"] is True
    assert "-Xmx2048m" in _read(os_dir, "docker-compose.yml")
    assert set(run.users) == {"toka", "tokb"}


def test_an_operator_override_is_included(tmp_path):
    run, os_dir = FakeCluster(), tmp_path / "opensearch"
    os_dir.mkdir()
    (os_dir / mod.OVERRIDE_FILE).write_text("services: {}\n")
    _sync(run, os_dir, "a", _instance(tmp_path, "a"))
    up = run.compose_calls()[0]
    assert up[up.index(mod.OVERRIDE_FILE) - 1] == "-f"
    assert up.index("docker-compose.yml") < up.index(mod.OVERRIDE_FILE)


def test_a_token_another_member_has_is_reported(tmp_path):
    run, os_dir = FakeCluster(), tmp_path / "opensearch"
    a = _instance(tmp_path, "a", token="same")
    b = _instance(tmp_path, "b", token="same")
    _sync(run, os_dir, "a", a)
    result = _sync(run, os_dir, "b", b)
    assert result["token_conflict"] == "a"
    assert run.users["same"]["attributes"] == {mod.INSTANCE_ATTRIBUTE: "a"}
    assert not os.path.exists(os.path.join(b, render.LAYER_FILE))


def test_opensearch_on_without_credentials_is_refused(tmp_path):
    a = _instance(tmp_path, "a", password=None)
    with pytest.raises(mod.OpenSearchError, match="CANASTA_OPENSEARCH_PASSWORD"):
        _sync(FakeCluster(), tmp_path / "opensearch", "a", a)


def test_an_instance_without_opensearch_creates_nothing(tmp_path):
    run, os_dir = FakeCluster(), tmp_path / "opensearch"
    result = _sync(run, os_dir, "a", _instance(tmp_path, "a", enabled=False))
    assert not result["changed"]
    assert not os.path.exists(os_dir)
    assert run.calls == []


# --- leaving ------------------------------------------------------------- #

def test_leaving_removes_the_instance_but_keeps_the_cluster(tmp_path):
    run = FakeCluster(counts={"toka_main_content": 5, "tokb_main_content": 7})
    os_dir = tmp_path / "opensearch"
    a, b = _instance(tmp_path, "a"), _instance(tmp_path, "b")
    _sync(run, os_dir, "a", a)
    _sync(run, os_dir, "b", b)

    _set_env(a, CANASTA_ENABLE_OPENSEARCH="false")
    result = _sync(run, os_dir, "a", a)
    assert result["removed_tokens"] == ["toka"]
    assert run.running
    assert set(run.users) == {"tokb"} and set(run.roles) == {"tokb"}
    assert run.counts == {"tokb_main_content": 7}
    assert not os.path.exists(os.path.join(a, render.LAYER_FILE))
    assert not os.path.exists(os.path.join(a, render.GLOBAL_SETTINGS))
    assert not os.path.exists(os.path.join(a, render.WIKI_SETTINGS % "main"))
    assert yaml.safe_load(_read(os_dir, "members.yaml")) == {
        "members": [{"id": "b", "path": b}], "removed": []}


def test_the_last_member_leaving_stops_the_cluster(tmp_path):
    run = FakeCluster(counts={"toka_main_content": 5})
    os_dir = tmp_path / "opensearch"
    a = _instance(tmp_path, "a")
    _sync(run, os_dir, "a", a)
    result = _sync(run, os_dir, "a", a, member="absent")
    assert result["removed_tokens"] == ["toka"]
    assert not run.running
    assert run.counts == {}
    assert ["docker", "compose", "-p", render.PROJECT, "-f", "docker-compose.yml",
            "down"] in run.calls


def test_a_member_whose_directory_is_gone_is_dropped(tmp_path):
    run, os_dir = FakeCluster(), tmp_path / "opensearch"
    a, b = _instance(tmp_path, "a"), _instance(tmp_path, "b")
    _sync(run, os_dir, "a", a)
    os.rename(a, str(tmp_path / "moved"))
    result = _sync(run, os_dir, "b", b)
    assert result["members"] == ["b"]
    assert set(run.users) == {"tokb"}


def test_only_canasta_users_are_deleted(tmp_path):
    run = FakeCluster(users={
        "operator": {"description": "added by hand"},
        "old": {"attributes": {mod.INSTANCE_ATTRIBUTE: "gone"}},
    })
    _sync(run, tmp_path / "opensearch", "a", _instance(tmp_path, "a"))
    assert set(run.users) == {"operator", "toka"}


def test_a_new_ca_makes_new_certificates_and_reinstalls_them(tmp_path):
    run, os_dir = FakeCluster(), tmp_path / "opensearch"
    a = _instance(tmp_path, "a")
    _sync(run, os_dir, "a", a)
    os.remove(os_dir / "ca.key")
    result = _sync(run, os_dir, "a", a)
    assert result["restarted"] is True
    up = run.compose_calls()[-1]
    assert "--force-recreate" in up
