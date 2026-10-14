"""A K8s gitops init refused over a mismatched sops-age key leaves no checkout.

The refusal tells the operator to fix the key and re-run. If init had already
written the chart, hosts/ and an empty .git, that re-run would stop at
"GitOps already initialized" instead.
"""

import os
import stat
import subprocess
import sys

import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ROLES = os.path.join(REPO_ROOT, "roles")
COMMON = os.path.join(ROLES, "common")

CONTROLLER_RECIPIENT = "age1controllerkeyrecipient"
CLUSTER_RECIPIENT = "age1clusterkeyrecipient"
SSH_REPO = "git@example.com:org/gitops.git"

# Shadows kubernetes.core so the cluster read returns a sops-age Secret that
# holds a key other than the controller's.
K8S_INFO = '''#!/usr/bin/python
from ansible.module_utils.basic import AnsibleModule
import base64


def main():
    m = AnsibleModule(argument_spec=dict(kind=dict(), name=dict(),
                                         namespace=dict()))
    data = base64.b64encode(b"AGE-SECRET-KEY-CLUSTER\\n").decode()
    m.exit_json(changed=False, resources=[
        {"metadata": {"name": m.params["name"]},
         "data": {"keys.txt": data}}])


main()
'''

K8S = '''#!/usr/bin/python
from ansible.module_utils.basic import AnsibleModule


def main():
    m = AnsibleModule(argument_spec=dict(
        state=dict(), definition=dict(type="raw"), apply=dict(type="bool"),
        kind=dict(), name=dict(), namespace=dict()))
    m.exit_json(changed=False)


main()
'''

AGE_KEYGEN = f'''#!/bin/sh
case "$1" in
  --version) echo v1.2.1 ;;
  -y)
    if [ -n "$2" ]; then echo {CONTROLLER_RECIPIENT};
    else cat >/dev/null; echo {CLUSTER_RECIPIENT}; fi ;;
  -o) echo AGE-SECRET-KEY-NEW > "$2" ;;
esac
'''


def _write_exec(path, content):
    path.write_text(content)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _fake_collection(base):
    mods = base / "ansible_collections" / "kubernetes" / "core" / "plugins" / "modules"
    mods.mkdir(parents=True)
    (base / "ansible_collections" / "kubernetes" / "core" / "galaxy.yml").write_text(
        "namespace: kubernetes\nname: core\nversion: 99.0.0\n")
    (mods / "k8s_info.py").write_text(K8S_INFO)
    (mods / "k8s.py").write_text(K8S)


def _instance(tmp_path):
    inst = tmp_path / "inst"
    (inst / "config" / "settings" / "global").mkdir(parents=True)
    (inst / "values.yaml").write_text("domains:\n- localhost\n")
    (inst / ".env").write_text("MW_SITE_SERVER=https://localhost\n")
    (inst / "config" / "wikis.yaml").write_text(
        "wikis:\n- id: main\n  url: localhost\n  name: Main\n")
    (inst / "config" / "Caddyfile").write_text("")
    (inst / "config" / "default.vcl").write_text("")
    return inst


def _run_init(tmp_path):
    inst = _instance(tmp_path)
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)

    config_dir = tmp_path / "config"
    (config_dir / "sops").mkdir(parents=True)
    (config_dir / "sops" / "h1.age").write_text("AGE-SECRET-KEY-CONTROLLER\n")

    bindir = tmp_path / "bin"
    bindir.mkdir()
    _write_exec(bindir / "age-keygen", AGE_KEYGEN)
    collections = tmp_path / "collections"
    _fake_collection(collections)

    gitconfig = tmp_path / "gitconfig"
    # Kubernetes init takes only SSH URLs; rewrite one to the local remote.
    gitconfig.write_text("[user]\n\tname = t\n\temail = t@example.com\n"
                         "[init]\n\tdefaultBranch = main\n"
                         f'[url "{remote}"]\n\tinsteadOf = {SSH_REPO}\n')

    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars_files": [os.path.join(REPO_ROOT, "vars", "secret_classification.yml")],
        "vars": {
            "ansible_python_interpreter": sys.executable,
            "canasta_root": REPO_ROOT,
            "instance_path": str(inst),
            "instance_id": "t1",
            "host_name": "h1",
            "repo": SSH_REPO,
            "key": str(tmp_path / "keys" / "deploy"),
            "encrypt_secrets": True,
            "non_interactive": True,
            "argocd_namespace": "argocd",
        },
        "tasks": [{"ansible.builtin.include_role": {
            "name": "gitops", "tasks_from": "init_kubernetes"}}],
    }]))
    env = dict(
        os.environ,
        PATH=f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}",
        CANASTA_CONFIG_DIR=str(config_dir),
        GIT_CONFIG_GLOBAL=str(gitconfig),
        ANSIBLE_NOCOLOR="1",
        ANSIBLE_LOCALHOST_WARNING="0",
        ANSIBLE_ROLES_PATH=ROLES,
        ANSIBLE_COLLECTIONS_PATH=str(collections),
        ANSIBLE_FILTER_PLUGINS=os.path.join(REPO_ROOT, "filter_plugins"),
        ANSIBLE_LIBRARY=os.pathsep.join([
            os.path.join(COMMON, "library"),
            os.path.join(ROLES, "extensions_skins", "library")]),
        ANSIBLE_MODULE_UTILS=os.path.join(COMMON, "module_utils"),
    )
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env,
                          cwd=tmp_path)
    return inst, proc


def test_mismatched_cluster_key_refuses_before_creating_the_checkout(tmp_path):
    inst, proc = _run_init(tmp_path)
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, out
    assert "differs from the key in this" in out, out
    assert CLUSTER_RECIPIENT in out, out
    for leftover in (".git", "hosts", "Chart.yaml", "templates",
                     "values.template.yaml", "argocd", ".gitignore"):
        assert not (inst / leftover).exists(), (
            f"init left {leftover} behind after refusing:\n{out}")
