"""Structural guards for the in-cluster image registry that makes
`canasta create --build-from` (and, later, custom service/sidecar images)
work on Kubernetes.

A full install e2e needs a systemd k3s host, which a single CI runner can't
provide. These tests instead lock in the wiring so the pieces can't silently
regress:

  - the registry is a fixed-ClusterIP service (cluster-internal, NOT a
    NodePort) so the auth-less registry isn't internet-exposed and no
    kube-proxy lockdown / k3s restart is needed;
  - push uses a loopback hostPort on the control plane (auto-insecure, no
    daemon.json);
  - every node trusts the registry via a certs.d DaemonSet (hot-reloaded, no
    restart) — including workers that join later;
  - the cp install deploys both the registry and the trust DaemonSet;
  - a build_from image is referenced at the registry, not the bare local tag.
"""

import os

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
CP = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks", "k8s_install_k3s.yml")
AGENT = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "k8s_install_k3s_agent.yml")
REGISTRY_MANIFEST = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "files", "k8s_registry.yaml")
TRUST_MANIFEST = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "files", "k8s_registry_trust.yaml")
VALUES_TPL = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "templates", "k8s_values.yaml.j2")
PUSH = os.path.join(REPO_ROOT, "roles", "create", "tasks", "_kubernetes_setup.yml")
CMD_DEFS = os.path.join(REPO_ROOT, "meta", "command_definitions.yml")

REGISTRY_ADDR = "10.43.0.2:5000"


def _walk(tasks):
    for t in tasks or []:
        if not isinstance(t, dict):
            continue
        yield t
        for nested in ("block", "rescue", "always"):
            if nested in t:
                yield from _walk(t[nested])


def _text(path):
    with open(path) as f:
        return f.read()


def test_registry_is_a_fixed_clusterip_not_a_nodeport():
    docs = [d for d in yaml.safe_load_all(_text(REGISTRY_MANIFEST)) if d]
    svc = next(d for d in docs if d["kind"] == "Service")
    assert svc["spec"]["type"] == "ClusterIP"
    assert svc["spec"]["clusterIP"] == "10.43.0.2"
    # No NodePort type / nodePort field — that's what would expose it publicly.
    assert all(d.get("spec", {}).get("type") != "NodePort" for d in docs)
    assert all("nodePort" not in p
               for p in svc["spec"]["ports"])


def test_registry_push_path_is_loopback_hostport_on_the_cp():
    docs = [d for d in yaml.safe_load_all(_text(REGISTRY_MANIFEST)) if d]
    dep = next(d for d in docs if d["kind"] == "Deployment")
    spec = dep["spec"]["template"]["spec"]
    assert "node-role.kubernetes.io/control-plane" in spec["nodeSelector"]
    port = spec["containers"][0]["ports"][0]
    assert port["hostPort"] == 5000 and port["hostIP"] == "127.0.0.1"


def test_trust_daemonset_writes_certsd_on_every_node():
    docs = [d for d in yaml.safe_load_all(_text(TRUST_MANIFEST)) if d]
    ds = next(d for d in docs if d["kind"] == "DaemonSet")
    pod = ds["spec"]["template"]["spec"]
    # tolerations: Exists -> lands on every node incl. the control plane
    assert any(t.get("operator") == "Exists" for t in pod["tolerations"])
    vol = next(v for v in pod["volumes"] if "hostPath" in v)
    assert vol["hostPath"]["path"].endswith("/containerd/certs.d")
    body = _text(TRUST_MANIFEST)
    assert REGISTRY_ADDR in body and "skip_verify = true" in body


def test_cp_deploys_registry_and_trust():
    cmds = [(t.get("ansible.builtin.command", {}) or {}).get("cmd", "")
            for t in _walk(yaml.safe_load(open(CP)))]
    deploy = next(c for c in cmds if "k8s_registry.yaml" in c)
    assert "k8s_registry_trust.yaml" in deploy


def test_no_nodeport_lockdown_or_restart_machinery():
    # The ClusterIP design must not reintroduce NodePort exposure handling.
    cp = _text(CP)
    assert "nodeport-addresses" not in cp
    assert "registries.yaml" not in cp


def test_worker_has_no_per_node_registry_config():
    # Trust is cluster-wide via the DaemonSet; the worker join carries none.
    agent = _text(AGENT)
    assert "registries.yaml" not in agent
    assert "nodeport-addresses" not in agent


def test_build_from_image_points_at_the_registry():
    text = _text(VALUES_TPL)
    # build_from selects {{ registry }}/{{ _built_image }} (the ClusterIP),
    # not the bare canasta_image default; tag split must be last-colon so a
    # host:port/repo:tag address parses.
    assert "_built_image" in text and "registry" in text
    assert "regex_replace('^.*:', '')" in text


def test_push_uses_loopback_hostport():
    assert "localhost:5000/" in _text(PUSH)


def test_registry_default_is_the_clusterip():
    assert REGISTRY_ADDR in _text(CMD_DEFS)
