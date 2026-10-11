"""`canasta scale` persists the count and deploys through helm_deploy.yml.

The playbook runs on localhost against a stub canasta_root whose
resolve_instance.yml and helm_deploy.yml record what they were given, so
the test sees the values.yaml the deploy would read.
"""

import os
import subprocess
import sys

import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PLAYBOOK = os.path.join(REPO_ROOT, "playbooks", "scale.yml")
DEFINITIONS = os.path.join(REPO_ROOT, "meta", "command_definitions.yml")


def _stub_root(tmp_path, orchestrator):
    root = tmp_path / "root"
    common = root / "roles" / "common" / "tasks"
    orch = root / "roles" / "orchestrator" / "tasks"
    common.mkdir(parents=True)
    orch.mkdir(parents=True)
    (common / "resolve_instance.yml").write_text(yaml.safe_dump([{
        "ansible.builtin.set_fact": {
            "instance_id": "{{ id }}",
            "instance_path": str(tmp_path / "inst"),
            "instance_orchestrator": orchestrator,
        },
    }]))
    (orch / "helm_deploy.yml").write_text(yaml.safe_dump([{
        "ansible.builtin.copy": {
            "content": "{{ {'namespace': _k8s_namespace, 'values':"
                       " lookup('file', instance_path ~ '/values.yaml')"
                       " | from_yaml} | to_json }}",
            "dest": str(tmp_path / "deployed.json"),
        },
    }]))
    return root


def _run(tmp_path, values, replicas, component="web",
         orchestrator="kubernetes"):
    inst = tmp_path / "inst"
    inst.mkdir()
    (inst / "values.yaml").write_text(values)
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {
            "canasta_root": str(_stub_root(tmp_path, orchestrator)),
            "id": "mysite",
            "replicas": replicas,
            "component": component,
        },
        "tasks": [{"ansible.builtin.include_tasks": PLAYBOOK}],
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_STDOUT_CALLBACK="default")
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run(
        [playbook, "-i", "localhost,", str(play)],
        capture_output=True, text=True, env=env, cwd=tmp_path)
    deployed = tmp_path / "deployed.json"
    return proc, (yaml.safe_load(deployed.read_text())
                  if deployed.exists() else None)


class TestScalePlaybook:
    def test_persists_count_then_deploys_through_helm_deploy(self, tmp_path):
        proc, deployed = _run(
            tmp_path,
            "instance:\n  id: mysite\nweb:\n  replicaCount: 1\n"
            "domains:\n  - wiki.example.com\n",
            2)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert deployed["namespace"] == "canasta-mysite"
        assert deployed["values"]["web"]["replicaCount"] == 2
        assert deployed["values"]["domains"] == ["wiki.example.com"]
        assert deployed["values"]["instance"] == {"id": "mysite"}
        assert "Scaled web to 2 replica(s)" in proc.stdout + proc.stderr

    def test_adds_web_section_when_absent(self, tmp_path):
        proc, deployed = _run(tmp_path, "instance:\n  id: mysite\n", 3)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert deployed["values"]["web"] == {"replicaCount": 3}

    def test_no_deploy_when_count_unchanged(self, tmp_path):
        proc, deployed = _run(tmp_path, "web:\n  replicaCount: 3\n", 3)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert deployed is None
        assert "already 3; nothing to do" in proc.stdout + proc.stderr

    def test_refuses_compose(self, tmp_path):
        proc, deployed = _run(tmp_path, "web:\n  replicaCount: 1\n", 2,
                              orchestrator="compose")
        assert proc.returncode != 0
        assert deployed is None
        assert "Kubernetes-only" in proc.stdout + proc.stderr

    def test_refuses_other_components(self, tmp_path):
        proc, deployed = _run(tmp_path, "web:\n  replicaCount: 1\n", 2,
                              component="varnish")
        assert proc.returncode != 0
        assert deployed is None
        assert "only 'web'" in (proc.stdout + proc.stderr).lower()

    def test_refuses_zero(self, tmp_path):
        proc, deployed = _run(tmp_path, "web:\n  replicaCount: 1\n", 0)
        assert proc.returncode != 0
        assert deployed is None

    def test_refuses_scalar_web_section(self, tmp_path):
        proc, deployed = _run(tmp_path, "web: disabled\n", 2)
        assert proc.returncode != 0
        assert deployed is None
        assert "web: disabled" in (tmp_path / "inst" / "values.yaml").read_text()


def test_scale_routes_to_its_playbook():
    with open(DEFINITIONS) as f:
        commands = {c["name"]: c for c in yaml.safe_load(f)["commands"]}
    scale = commands["scale"]
    assert scale.get("playbook") == "scale.yml"
    assert not scale.get("direct_only", False)
    assert os.path.isfile(PLAYBOOK)


def test_playbook_deploys_through_the_shared_helm_deploy():
    with open(PLAYBOOK) as f:
        text = f.read()
    assert "roles/orchestrator/tasks/helm_deploy.yml" in text
    assert "helm upgrade" not in text
