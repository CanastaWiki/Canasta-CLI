"""A changed Secret value rolls the pods that read it after a gitops sync.

Pods read Secrets through secretKeyRef, which a changed value does not roll,
and the gitops repo holds no Secret values, so a push of a new secret
changed nothing Argo CD deploys. Push now hashes the values each workload
reads into this host's rendered values, and the chart makes the hashes pod
annotations. A direct deploy of a gitops instance carries the same hashes.
"""

import base64
import os
import shutil
import subprocess
import sys

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..",
                                "filter_plugins"))
from canasta_gitops import canasta_secret_checksums  # noqa: E402

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CHART = os.path.join(REPO_ROOT, "roles", "orchestrator", "files", "helm",
                     "canasta")
GITOPS = os.path.join(REPO_ROOT, "roles", "gitops", "tasks")
RENDER = os.path.join(GITOPS, "render_kubernetes.yml")
PUSH = os.path.join(GITOPS, "push_kubernetes.yml")
HELM_DEPLOY = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks",
                           "helm_deploy.yml")
COMMON = os.path.join(REPO_ROOT, "roles", "common")


def _b64(value):
    return base64.b64encode(value.encode()).decode()


def _secret(name, **data):
    return {"metadata": {"name": name},
            "data": {k: _b64(v) for k, v in data.items()}}


def _secrets(app=None, crowdsec="bouncer-key", db="dbpw", mw="mwkey"):
    return [
        _secret("inst-db-credentials", MYSQL_PASSWORD=db,
                MYSQL_ROOT_PASSWORD="root"),
        _secret("inst-mw-secrets", MW_SECRET_KEY=mw),
        _secret("inst-app-secrets", **(app or {"MAPS": "m1", "TOKEN": "t1"})),
        _secret("canasta-inst-crowdsec", CROWDSEC_BOUNCER_API_KEY=crowdsec),
        _secret("canasta-inst-backup-env", RESTIC_PASSWORD="r"),
    ]


VALUES = {"appSecretEnv": ["MAPS"],
          "sidecars": [{"name": "cache", "envSecret": ["TOKEN"]},
                       {"name": "plain", "env": {"A": "b"}}]}


def _sums(secrets=None, values=None):
    return canasta_secret_checksums(
        _secrets() if secrets is None else secrets,
        VALUES if values is None else values, "inst")


class TestFingerprint:
    def test_stable_when_nothing_changes(self):
        assert _sums() == _sums()
        assert _sums() == _sums(list(reversed(_secrets())))

    def test_holds_no_secret_material(self):
        text = yaml.safe_dump(_sums())
        for value in ("dbpw", "mwkey", "m1", "t1", "bouncer-key"):
            assert value not in text and _b64(value) not in text

    def test_a_web_secret_change_rolls_only_the_web(self):
        before, after = _sums(), _sums(_secrets(app={"MAPS": "m2", "TOKEN": "t1"}))
        assert before["web"] != after["web"]
        assert before["caddy"] == after["caddy"]
        assert before["sidecars"] == after["sidecars"]

    def test_a_sidecar_secret_change_rolls_only_that_sidecar(self):
        before, after = _sums(), _sums(_secrets(app={"MAPS": "m1", "TOKEN": "t2"}))
        assert before["web"] == after["web"]
        assert before["sidecars"]["cache"] != after["sidecars"]["cache"]
        assert set(after["sidecars"]) == {"cache"}

    def test_the_bouncer_key_rolls_caddy(self):
        assert _sums()["caddy"] != _sums(_secrets(crowdsec="new"))["caddy"]

    def test_the_database_password_rolls_the_web(self):
        assert _sums()["web"] != _sums(_secrets(db="new"))["web"]

    def test_an_unread_key_rolls_nothing(self):
        secrets = _secrets()
        secrets[0]["data"]["MYSQL_ROOT_PASSWORD"] = _b64("other")
        secrets[4]["data"]["RESTIC_PASSWORD"] = _b64("other")
        assert _sums(secrets) == _sums()

    def test_named_secrets_are_followed(self):
        values = dict(VALUES, secrets={"dbSecretName": "custom-db"})
        secrets = _secrets() + [_secret("custom-db", MYSQL_PASSWORD="x")]
        changed = [dict(s) for s in secrets]
        changed[-1] = _secret("custom-db", MYSQL_PASSWORD="y")
        assert _sums(secrets, values)["web"] != _sums(changed, values)["web"]

    def test_missing_secrets_still_hash(self):
        sums = _sums([], {})
        assert sums["web"] and sums["caddy"] and sums["sidecars"] == {}


# --- Chart -----------------------------------------------------------------

def _render(*args):
    if not shutil.which("helm"):
        pytest.skip("helm is not installed")
    out = subprocess.run(
        ["helm", "template", "canasta", CHART, "--set", "instance.id=inst",
         "--set", "sidecars[0].name=cache", "--set", "sidecars[0].image=redis",
         *args], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    docs = [d for d in yaml.safe_load_all(out.stdout) if d]
    return {d["metadata"]["name"]: d["spec"]["template"]["metadata"].get(
        "annotations") or {} for d in docs if d.get("kind") == "Deployment"}


SET = ["--set-string", "secretChecksums.web=w1",
       "--set-string", "secretChecksums.caddy=c1",
       "--set-string", "secretChecksums.sidecars.cache=s1"]


class TestChart:
    def test_the_hashes_become_pod_annotations(self):
        pods = _render("--set", "crowdsec.enabled=true", *SET)
        assert pods["canasta-inst-web"]["checksum/secrets"] == "w1"
        assert pods["canasta-inst-caddy"]["checksum/secrets"] == "c1"
        assert pods["canasta-inst-cache"]["checksum/secrets"] == "s1"

    def test_no_hashes_render_no_annotation(self):
        pods = _render("--set", "crowdsec.enabled=true")
        assert all("checksum/secrets" not in a for a in pods.values())

    def test_caddy_without_crowdsec_reads_no_secret(self):
        assert "checksum/secrets" not in _render(*SET)["canasta-inst-caddy"]


# --- Render ----------------------------------------------------------------

FAKE_K8S_INFO = '''#!/usr/bin/python
import json, os
from ansible.module_utils.basic import AnsibleModule
m = AnsibleModule(argument_spec=dict(kind=dict(), namespace=dict()))
with open(os.environ["FAKE_SECRETS"]) as f:
    m.exit_json(changed=False, resources=json.load(f))
'''


def _render_play(tmp_path, compute, previous=None):
    import json
    inst = tmp_path / "inst"
    (inst / "hosts" / "host1").mkdir(parents=True)
    (inst / ".gitops-host").write_text("host1\n")
    (inst / "hosts" / "host1" / "vars.yaml").write_text("instance_id: inst\n")
    (inst / "values.template.yaml").write_text(yaml.safe_dump(dict(
        VALUES, domains=["example.com"], image={"tag": "1"})))
    if previous is not None:
        (inst / "hosts" / "host1" / "rendered-values.yaml").write_text(
            yaml.safe_dump({"secretChecksums": previous}))
    mods = tmp_path / "ac" / "ansible_collections" / "kubernetes" / "core" \
        / "plugins" / "modules"
    mods.mkdir(parents=True)
    (mods / "k8s_info.py").write_text(FAKE_K8S_INFO)
    (tmp_path / "secrets.json").write_text(json.dumps(_secrets()))
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost", "connection": "local", "gather_facts": False,
        "vars": {"ansible_python_interpreter": sys.executable,
                 "instance_path": str(inst), "instance_id": "inst",
                 "render_secret_checksums": compute},
        "tasks": [{"ansible.builtin.include_tasks": RENDER}],
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_COLLECTIONS_PATH=str(tmp_path / "ac"),
               FAKE_SECRETS=str(tmp_path / "secrets.json"),
               ANSIBLE_FILTER_PLUGINS=os.path.join(REPO_ROOT, "filter_plugins"),
               ANSIBLE_LIBRARY=os.path.join(COMMON, "library"),
               ANSIBLE_MODULE_UTILS=os.path.join(COMMON, "module_utils"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env, cwd=tmp_path,
                          stdin=subprocess.DEVNULL)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    rendered = yaml.safe_load(
        (inst / "hosts" / "host1" / "rendered-values.yaml").read_text())
    template = yaml.safe_load((inst / "values.template.yaml").read_text())
    return proc.stdout, rendered, template


class TestRender:
    def test_push_writes_this_hosts_hashes(self, tmp_path):
        out, rendered, template = _render_play(tmp_path, True)
        expected = canasta_secret_checksums(_secrets(), rendered, "inst")
        assert rendered["secretChecksums"] == expected
        assert "secretChecksums" not in template
        assert "dbpw" not in out and _b64("dbpw") not in out

    def test_other_renders_keep_the_last_pushed_hashes(self, tmp_path):
        prev = {"web": "w0", "caddy": "c0", "sidecars": {"cache": "s0"}}
        _, rendered, _ = _render_play(tmp_path, False, previous=prev)
        assert rendered["secretChecksums"] == prev

    def test_a_first_render_without_a_push_has_no_hashes(self, tmp_path):
        _, rendered, _ = _render_play(tmp_path, False)
        assert rendered["secretChecksums"] == {}

    def test_push_asks_the_render_for_hashes(self):
        with open(PUSH) as f:
            tasks = yaml.safe_load(f)
        render = next(t for t in tasks if t.get("name") == "Render per-host values")
        assert render["vars"]["render_secret_checksums"] is True


# --- Direct deploy ---------------------------------------------------------

def test_a_direct_deploy_of_a_gitops_instance_carries_the_hashes():
    with open(HELM_DEPLOY) as f:
        text = f.read()
        tasks = yaml.safe_load(text)
    block = next(t for t in tasks
                 if t.get("name") == "Write the Secret checksums of a gitops instance")
    assert block["when"] == "_hd_gitops.stat.exists"
    assert "canasta_secret_checksums" in yaml.safe_dump(block)
    assert all(t.get("no_log") for t in block["block"][1:])
    assert "values-checksums.yaml', '')" in text


# --- Ignored in the gitops repo --------------------------------------------

def _git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True,
                          text=True, check=True).stdout


def test_an_upgraded_repo_ignores_the_checksums_override(tmp_path):
    from _orchestrator import INSTANCE_IS_K8S_VARS
    inst = tmp_path / "inst"
    inst.mkdir()
    (inst / ".gitops-host").write_text("host1\n")
    # A repo whose .gitignore predates the rule.
    (inst / ".gitignore").write_text(".gitops-host\nvalues-domains.yaml\n")
    _git(inst, "init", "-q")
    _git(inst, "add", ".gitignore")
    _git(inst, "-c", "user.name=t", "-c", "user.email=t@example.com",
         "-c", "commit.gpgsign=false", "commit", "-q", "-m", "init")
    (inst / "values-checksums.yaml").write_text("secretChecksums: {}\n")
    assert "values-checksums.yaml" in _git(inst, "status", "--porcelain")
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost", "connection": "local", "gather_facts": False,
        "vars": dict(INSTANCE_IS_K8S_VARS, instance_path=str(inst),
                     canasta_root=REPO_ROOT, instance_orchestrator="kubernetes",
                     ansible_python_interpreter=sys.executable),
        "tasks": [{"ansible.builtin.include_tasks": os.path.join(
            GITOPS, "refresh_gitignore.yml")}],
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_CONFIG=os.path.join(REPO_ROOT, "ansible.cfg"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env, cwd=REPO_ROOT)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "values-checksums.yaml" in (inst / ".gitignore").read_text().split()
    assert "values-checksums.yaml" not in _git(inst, "status", "--porcelain")
