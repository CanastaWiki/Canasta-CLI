"""A SOPS gitops push encrypts the Secrets built from the instance's files.

Once a Secret is in git, Argo CD self-heal can restore its committed value
before push reads the cluster, so the Secrets k8s_sync_config builds from
local files (app secrets, backup env, CrowdSec key) take their data from those
files. The cluster read is supplied as a play var, and a stand-in sops
encrypts with a random IV the way the real one does.
"""

import base64
import os
import subprocess
import sys

import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO_ROOT, "filter_plugins"))

from canasta_secrets import canasta_overlay_local_secrets  # noqa: E402

GITOPS = os.path.join(REPO_ROOT, "roles", "gitops", "tasks")
ORCH = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks")
COMMON = os.path.join(REPO_ROOT, "roles", "common")
HOST = "prod"
APP = "mysite-app-secrets"
DB = "mysite-db-credentials"

FAKE_SOPS = """#!/usr/bin/env python3
import os, sys
if sys.argv[1:] == ["--version"]:
    print("sops 3.9.0 (fake)")
    sys.exit(0)
path = sys.argv[-1]
out, in_data = [], False
for line in open(path).read().splitlines():
    if line == "data:":
        in_data = True
    elif in_data and line.startswith("  ") and ": " in line:
        key, value = line.split(": ", 1)
        line = "%s: ENC[AES256_GCM,data:%s,iv:%s]" % (
            key, value, os.urandom(4).hex())
    out.append(line)
out.append("sops:")
out.append("    version: 3.9.0")
open(path, "w").write("\\n".join(out) + "\\n")
"""


def _b64(text):
    return base64.b64encode(text.encode()).decode()


def _secret(name, data):
    return {"metadata": {"name": name}, "type": "Opaque",
            "data": {k: _b64(v) for k, v in data.items()}}


def _play(tmp_path, tasks, play_vars, extra_env=None):
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars_files": [os.path.join(REPO_ROOT, "vars",
                                    "secret_classification.yml")],
        "vars": dict({"canasta_root": REPO_ROOT,
                      "ansible_python_interpreter": sys.executable},
                     **play_vars),
        "tasks": tasks,
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_FILTER_PLUGINS=os.path.join(REPO_ROOT, "filter_plugins"),
               ANSIBLE_LIBRARY=os.path.join(COMMON, "library"),
               ANSIBLE_MODULE_UTILS=os.path.join(COMMON, "module_utils"),
               **(extra_env or {}))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env,
                          cwd=tmp_path)
    return proc.returncode, proc.stdout + proc.stderr


def _absolutize(tasks, base):
    """Point relative include_tasks at ``base`` and drop cluster writes."""
    out = []
    for task in tasks:
        if "kubernetes.core.k8s" in task:
            continue
        task = dict(task)
        inc = task.get("ansible.builtin.include_tasks")
        if isinstance(inc, str) and not inc.startswith(("/", "{{")):
            task["ansible.builtin.include_tasks"] = os.path.join(base, inc)
        for key in ("block", "rescue", "always"):
            if key in task:
                task[key] = _absolutize(task[key], base)
        out.append(task)
    return out


def _write_tasks():
    with open(os.path.join(GITOPS, "_write_sops_secrets.yml")) as f:
        tasks = yaml.safe_load(f)
    assert "kubernetes.core.k8s_info" in tasks[2]
    return _absolutize(tasks[:2] + tasks[3:], GITOPS)


def _instance(tmp_path):
    inst = tmp_path / "inst"
    (inst / "hosts" / HOST / "secrets").mkdir(parents=True)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    sops = bindir / "sops"
    sops.write_text(FAKE_SOPS.replace("/usr/bin/env python3", sys.executable))
    sops.chmod(0o755)
    return inst, {"PATH": "%s:%s" % (bindir, os.environ["PATH"])}


def _push(tmp_path, inst, env, cluster, local=None):
    play_vars = {"instance_path": str(inst), "host_name": HOST,
                 "_k8s_namespace": "canasta-mysite",
                 "_sops_ns_secrets": {"resources": cluster}}
    if local is not None:
        play_vars["_k8s_local_secrets"] = local
    rc, out = _play(tmp_path, _write_tasks(), play_vars, env)
    assert rc == 0, out


def _manifest(inst, name):
    return (inst / "hosts" / HOST / "secrets" / (name + ".enc.yaml")).read_text()


class TestOverlayFilter:
    def test_local_data_replaces_the_cluster_copy(self):
        cluster = [_secret(APP, {"API_KEY": "old"}), _secret(DB, {"P": "x"})]
        out = canasta_overlay_local_secrets(cluster, {APP: {"API_KEY": "new"}})
        by_name = {s["metadata"]["name"]: s for s in out}
        assert by_name[APP]["data"] == {"API_KEY": _b64("new")}
        assert by_name[DB] == cluster[1]
        assert len(out) == 2

    def test_local_secret_missing_from_the_cluster_is_added(self):
        out = canasta_overlay_local_secrets([], {APP: {"K": "v"}})
        assert out == [_secret(APP, {"K": "v"})]

    def test_no_local_data_leaves_the_cluster_listing(self):
        cluster = [_secret(DB, {"P": "x"})]
        assert canasta_overlay_local_secrets(cluster, None) == cluster

    def test_non_ascii_values_encode_as_utf8(self):
        out = canasta_overlay_local_secrets([], {APP: {"K": "pässwörd"}})
        assert base64.b64decode(out[0]["data"]["K"]).decode() == "pässwörd"


class TestPushEncryptsLocalValues:
    def test_changed_local_value_is_committed_despite_reverted_cluster(
            self, tmp_path):
        inst, env = _instance(tmp_path)
        old = [_secret(APP, {"API_KEY": "old"})]
        _push(tmp_path, inst, env, old)
        before = _manifest(inst, APP)
        assert "ENC[" in before

        # Argo CD restored the committed value, so the cluster still holds
        # "old" while secrets.env holds "new".
        _push(tmp_path, inst, env, old, {APP: {"API_KEY": "new"}})
        after = _manifest(inst, APP)
        assert after != before
        assert "data:" + _b64("new") + "," in after
        assert "data:" + _b64("old") + "," not in after

    def test_unchanged_value_leaves_the_file_untouched(self, tmp_path):
        inst, env = _instance(tmp_path)
        cluster = [_secret(APP, {"API_KEY": "same", "B": "2"}),
                   _secret(DB, {"MYSQL_PASSWORD": "pw"})]
        # The first push reads the cluster, as push did before local data.
        _push(tmp_path, inst, env, cluster)
        app_before, db_before = _manifest(inst, APP), _manifest(inst, DB)

        _push(tmp_path, inst, env, cluster,
              {APP: {"B": "2", "API_KEY": "same"}})
        assert _manifest(inst, APP) == app_before
        assert _manifest(inst, DB) == db_before

    def test_cluster_only_secrets_are_still_read_from_the_cluster(
            self, tmp_path):
        inst, env = _instance(tmp_path)
        _push(tmp_path, inst, env, [_secret(DB, {"MYSQL_PASSWORD": "pw1"})],
              {APP: {"K": "v"}})
        before = _manifest(inst, DB)
        _push(tmp_path, inst, env, [_secret(DB, {"MYSQL_PASSWORD": "pw2"})],
              {APP: {"K": "v"}})
        assert _manifest(inst, DB) != before
        assert "data:" + _b64("pw2") + "," in _manifest(inst, DB)

    def test_empty_namespace_does_not_prune_cluster_only_secrets(
            self, tmp_path):
        inst, env = _instance(tmp_path)
        _push(tmp_path, inst, env, [_secret(DB, {"MYSQL_PASSWORD": "pw"})])
        _push(tmp_path, inst, env, [], {APP: {"K": "v"}})
        assert (inst / "hosts" / HOST / "secrets" / (DB + ".enc.yaml")).exists()
        assert "ENC[" in _manifest(inst, APP)


def _sync_tasks():
    with open(os.path.join(ORCH, "k8s_sync_config.yml")) as f:
        return _absolutize(yaml.safe_load(f), ORCH)


def _sync_instance(tmp_path, env_lines, secrets_env):
    inst = tmp_path / "inst"
    (inst / "config" / "settings" / "global").mkdir(parents=True)
    (inst / ".env").write_text("\n".join(env_lines) + "\n")
    (inst / "config" / "wikis.yaml").write_text(
        "wikis:\n- id: main\n  url: example.com\n  name: Main\n")
    (inst / "config" / "Caddyfile").write_text("example.com {\n}\n")
    (inst / "config" / "default.vcl").write_text("vcl 4.1;\n")
    if secrets_env is not None:
        (inst / "config" / "secrets.env").write_text(secrets_env)
    return inst


class TestSyncRecordsLocalSecrets:
    def _local(self, tmp_path, inst):
        tasks = _sync_tasks() + [{
            "ansible.builtin.copy": {
                "content": "{{ _k8s_local_secrets | to_json }}",
                "dest": str(tmp_path / "local.json"), "mode": "0600"}}]
        rc, out = _play(tmp_path, tasks, {"instance_path": str(inst),
                                          "instance_id": "mysite"})
        assert rc == 0, out
        return yaml.safe_load((tmp_path / "local.json").read_text())

    def test_records_each_secret_built_from_the_files(self, tmp_path):
        inst = _sync_instance(tmp_path, [
            "MYSQL_PASSWORD=rootpw",
            "RESTIC_REPOSITORY=s3:example.com/bucket",
            "CANASTA_ENABLE_CROWDSEC=true",
            "CROWDSEC_BOUNCER_API_KEY=bouncer",
            "MW_SITE_FQDN=example.com",
        ], "API_KEY=new\n")
        local = self._local(tmp_path, inst)
        assert local[APP] == {"API_KEY": "new"}
        assert local["canasta-mysite-crowdsec"] == {
            "CROWDSEC_BOUNCER_API_KEY": "bouncer"}
        backup = local["canasta-mysite-backup-env"]
        assert backup["RESTIC_REPOSITORY"] == "s3:example.com/bucket"
        assert backup["MYSQL_PASSWORD"] == "rootpw"
        assert "MW_SITE_FQDN" not in backup

    def test_leaves_out_secrets_with_no_local_data(self, tmp_path):
        inst = _sync_instance(tmp_path, ["MW_SITE_FQDN=example.com"], None)
        assert self._local(tmp_path, inst) == {}
