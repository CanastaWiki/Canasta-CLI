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
ENSURE = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "k8s_ensure_registry.yml")
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


def test_ensure_task_applies_registry_and_trust():
    cmds = [(t.get("ansible.builtin.command", {}) or {}).get("cmd", "")
            for t in _walk(yaml.safe_load(open(ENSURE)))]
    apply = next(c for c in cmds if "k8s_registry.yaml" in c)
    assert "k8s_registry_trust.yaml" in apply


def test_ensure_stages_manifests_on_host_before_apply():
    # kubectl runs on the instance's host, where the controller's canasta_root
    # path doesn't exist — copy the manifests to the host first, then apply the
    # host-local copies, so a remote (-H) host works.
    body = _text(ENSURE)
    assert "ansible.builtin.copy" in body
    assert body.index("ansible.builtin.copy") < body.index("kubectl apply")
    assert "-f /tmp/k8s_registry.yaml" in body
    assert "{{ canasta_root }}/roles/orchestrator/files/k8s_registry.yaml" \
        not in body


def test_registry_is_ensured_from_install_upgrade_and_create():
    # Install, upgrade (self-heal existing clusters), and create --build-from
    # all ensure the registry, so build-from never dead-ends on a missing one.
    upgrade_main = os.path.join(REPO_ROOT, "roles", "upgrade", "tasks", "main.yml")
    for path in (CP, upgrade_main, PUSH):
        assert "k8s_ensure_registry.yml" in _text(path)


def test_install_deploys_registry_best_effort():
    # Cluster bootstrap must NOT hard-fail on the registry being slow to become
    # ready (it's only needed for a later build-from push).
    assert "registry_wait: false" in _text(CP)


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
    # build_from selects the registry ClusterIP (not the bare canasta_image
    # default); tag split must be last-colon so a host:port/repo:tag address
    # parses. The address is a literal here — see the consistency test below.
    assert "_built_image" in text and REGISTRY_ADDR in text
    assert "regex_replace('^.*:', '')" in text


def test_push_uses_loopback_hostport():
    assert "localhost:5000/" in _text(PUSH)


def test_registry_address_is_consistent_everywhere():
    # The pull address (10.43.0.2:5000) is hardcoded in three places that must
    # agree: the Service ClusterIP, the certs.d trust, and the pod image ref.
    # There is no --registry flag (overriding it would not be honored by the
    # Service/trust, so it was removed).
    assert "10.43.0.2" in _text(REGISTRY_MANIFEST)
    assert REGISTRY_ADDR in _text(TRUST_MANIFEST)
    assert REGISTRY_ADDR in _text(VALUES_TPL)
    assert "name: registry" not in _text(CMD_DEFS)


def test_image_ref_tag_split_parses_a_port_address():
    # Lock the parsing contract: host:port/repo:tag must split on the LAST colon.
    import re
    img = "10.43.0.2:5000/canasta:local"
    assert re.sub(r":[^:]+$", "", img) == "10.43.0.2:5000/canasta"   # repository
    assert re.sub(r"^.*:", "", img) == "local"                       # tag
    # and the default ghcr image (single colon) still parses
    assert re.sub(r"^.*:", "", "ghcr.io/canastawiki/canasta:3.5.10") == "3.5.10"


# --- upgrade path: build_from must work the same as create on k8s ---

UPGRADE_PUBLISH = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "upgrade_publish_rebuilt_image.yml")
UPGRADE_TAG = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "upgrade_image_tag.yml")
UPGRADE_MAIN = os.path.join(REPO_ROOT, "roles", "upgrade", "tasks", "main.yml")


def test_upgrade_pushes_rebuilt_image_to_the_registry():
    # Not the bare tag — must reach the registry via the loopback hostPort.
    assert "localhost:5000/" in _text(UPGRADE_PUBLISH)


def test_upgrade_keeps_the_registry_ref_for_build_from():
    # The k8s tag-bump must skip build_from (else it points at a ghcr version
    # tag the rebuilt local image doesn't have).
    assert "buildFrom | default('') == ''" in _text(UPGRADE_TAG)


def test_upgrade_restarts_rebuilt_build_from_instances():
    # No registry pull happens for build_from, so only a restart deploys the
    # rebuilt image. Gated on the rebuild having actually run — a build_from
    # instance whose source went missing was not rebuilt (see
    # test_upgrade_missing_build_from.py).
    assert "_upgrade_rebuild | bool" in _text(UPGRADE_MAIN)


def test_build_from_forces_a_repull():
    text = _text(VALUES_TPL)
    assert "pullPolicy" in text and "Always" in text


# --- API server readiness and failure messages ---

def _ensure_tasks():
    return list(_walk(yaml.safe_load(_text(ENSURE))))


def _task_named(prefix):
    return next(t for t in _ensure_tasks()
                if t.get("name", "").startswith(prefix))


def _render(template, **variables):
    import jinja2
    return " ".join(
        jinja2.Environment().from_string(template).render(**variables).split())


def test_ensure_waits_for_api_server_before_apply():
    names = [t.get("name", "") for t in _ensure_tasks()]
    wait = names.index("Wait for the Kubernetes API server to be ready")
    apply = names.index("Deploy the in-cluster image registry and per-node trust")
    assert wait < apply
    task = _task_named("Wait for the Kubernetes API server")
    assert "get --raw /readyz" in task["ansible.builtin.command"]["cmd"]
    assert "when" not in task
    assert task["failed_when"] is False
    assert task["retries"] * task["delay"] >= 120


def test_api_server_timeout_fails_with_a_clear_message():
    task = _task_named("Fail if the Kubernetes API server")
    msg = _render(task["ansible.builtin.fail"]["msg"], _registry_api_ready={
        "rc": 1, "stdout": "",
        "stderr": "dial tcp 127.0.0.1:6443: connect: connection refused"})
    assert msg.startswith(
        "The Kubernetes API server did not become ready")
    assert "connection refused" in msg
    assert "10.43" not in msg


def test_apply_still_retries_transient_errors():
    task = _task_named("Deploy the in-cluster image registry")
    assert task["retries"] >= 1 and "rc == 0" in task["until"]


def _apply_failure_msg(stderr):
    task = _task_named("Explain a registry deploy failure")
    return _render(task["ansible.builtin.fail"]["msg"],
                   _registry_apply={"rc": 1, "stdout": "", "stderr": stderr})


def test_apply_failure_blames_cidr_only_on_clusterip_errors():
    for stderr in (
            'Service "registry" is invalid: spec.clusterIP: Invalid value: '
            '"10.43.0.2": provided IP is not in the valid range',
            'Service "registry" is invalid: spec.clusterIPs: Invalid value: '
            '["10.43.0.2"]: failed to allocate IP 10.43.0.2: '
            'provided IP is already allocated'):
        msg = _apply_failure_msg(stderr)
        assert "service CIDR" in msg and "10.43.0.2 is already in use" in msg
        assert msg.endswith(stderr)


def test_apply_failure_reports_other_errors_without_cidr_guess():
    stderr = ('error validating "/tmp/k8s_registry.yaml": failed to download '
              'openapi: dial tcp 127.0.0.1:6443: connect: connection refused')
    msg = _apply_failure_msg(stderr)
    assert msg == ("Could not deploy the in-cluster image registry. kubectl: "
                   + stderr)
