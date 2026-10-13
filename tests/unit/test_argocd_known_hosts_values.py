"""A helm upgrade of Argo CD keeps the host keys merged into its known-hosts.

argocd-ssh-known-hosts-cm is chart-owned: an upgrade re-renders it from
configs.ssh.knownHosts and configs.ssh.extraHosts. The host keys merged in
for self-hosted repos must be carried through extraHosts, once each.
"""

import json
import os
import stat
import subprocess
import sys

import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TASKS = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks")
VALUES_TASKS = os.path.join(TASKS, "k8s_argocd_known_hosts_values.yml")
SIDECAR = os.path.join(TASKS, "k8s_ensure_sops_sidecar.yml")

CHART = ["github.com ssh-ed25519 AAAAgh", "gitlab.com ssh-ed25519 AAAAgl"]
SELF_HOSTED = ["git.example.com ssh-ed25519 AAAAone",
               "[git.example.org]:2222 ssh-rsa AAAAtwo"]


def _stub(bindir, name, fixture):
    path = bindir / name
    path.write_text("#!/bin/sh\ncat '%s'\n" % fixture)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _cm_json(text):
    return json.dumps({"data": {"ssh_known_hosts": text}}) if text is not None else ""


def _render_cm(values):
    # Mirrors the chart template: knownHosts, then extraHosts when non-empty.
    out = values["configs"]["ssh"]["knownHosts"].rstrip("\n")
    extra = values["configs"]["ssh"].get("extraHosts")
    if extra:
        out += "\n" + extra
    return out + "\n"


def _run(tmp_path, cm_text):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    helm_out = tmp_path / "helm.json"
    helm_out.write_text(json.dumps(
        {"configs": {"ssh": {"knownHosts": "\n".join(CHART) + "\n",
                             "extraHosts": ""}}}))
    cm_out = tmp_path / "cm.json"
    cm_out.write_text(_cm_json(cm_text))
    _stub(bindir, "helm", helm_out)
    _stub(bindir, "kubectl", cm_out)
    values_file = tmp_path / "values.yaml"
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {"canasta_root": REPO_ROOT,
                 "argocd_known_hosts_values_file": str(values_file),
                 "ansible_python_interpreter": sys.executable},
        "tasks": [{"ansible.builtin.include_tasks": VALUES_TASKS}],
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               PATH=str(bindir) + os.pathsep + os.environ["PATH"])
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env, cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    values = yaml.safe_load(values_file.read_text())
    return {"configs": {"ssh": {
        "knownHosts": "\n".join(CHART) + "\n",
        "extraHosts": values["configs"]["ssh"]["extraHosts"]}}}


def _lines(text):
    return [line for line in text.splitlines() if line.strip()]


def test_merged_host_keys_survive_the_upgrade(tmp_path):
    before = "\n".join(CHART + SELF_HOSTED) + "\n"
    after = _render_cm(_run(tmp_path, before))
    assert _lines(after) == _lines(before)


def test_repeated_upgrades_never_duplicate_entries(tmp_path):
    cm = "\n".join(CHART + SELF_HOSTED) + "\n"
    for _ in range(3):
        cm = _render_cm(_run(tmp_path, cm))
    lines = _lines(cm)
    assert len(lines) == len(set(lines))
    assert set(lines) == set(CHART + SELF_HOSTED)


def test_duplicate_lines_in_the_configmap_collapse(tmp_path):
    cm = "\n".join(CHART + SELF_HOSTED + SELF_HOSTED[:1]) + "\n"
    values = _run(tmp_path, cm)
    assert _lines(values["configs"]["ssh"]["extraHosts"]) == SELF_HOSTED


def test_only_chart_defaults_add_nothing(tmp_path):
    values = _run(tmp_path, "\n".join(CHART) + "\n")
    assert values["configs"]["ssh"]["extraHosts"] == ""


def test_missing_configmap_adds_nothing(tmp_path):
    values = _run(tmp_path, None)
    assert values["configs"]["ssh"]["extraHosts"] == ""


def test_sidecar_upgrade_passes_the_known_hosts_values():
    with open(SIDECAR) as f:
        tasks = yaml.safe_load(f)
    stage = next(t for t in tasks
                 if "k8s_argocd_known_hosts_values.yml"
                 in str(t.get("ansible.builtin.include_tasks", "")))
    path = stage["vars"]["argocd_known_hosts_values_file"]
    upgrade = next(t for t in tasks
                   if "helm upgrade argocd" in t.get("vars", {}).get("rx_cmd", ""))
    assert tasks.index(stage) < tasks.index(upgrade)
    assert ("-f " + path) in upgrade["vars"]["rx_cmd"]
