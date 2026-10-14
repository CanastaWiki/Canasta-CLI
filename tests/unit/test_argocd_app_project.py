"""Each gitops Kubernetes instance's Argo CD Application lives in its own
AppProject, limited to the instance's repo and namespace.

New Applications name the instance's project, which is applied before them.
Upgrade moves an existing Application out of 'default' without touching its
resources, and delete removes the project with the Application.
"""

import json
import os
import subprocess
import sys

import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
GITOPS = os.path.join(REPO_ROOT, "roles", "gitops", "tasks")
INIT = os.path.join(GITOPS, "init_kubernetes.yml")
JOIN = os.path.join(GITOPS, "join_kubernetes.yml")
APPLY_APP = os.path.join(GITOPS, "_apply_argo_application.yml")
APPLY_PROJECT = os.path.join(GITOPS, "_apply_argo_project.yml")
MIGRATE = os.path.join(GITOPS, "migrate_argo_project.yml")
UPGRADE = os.path.join(REPO_ROOT, "roles", "upgrade", "tasks", "main.yml")
DELETE = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks",
                      "delete_remove_argocd_app.yml")
CHART_APP = os.path.join(REPO_ROOT, "roles", "orchestrator", "files", "helm",
                         "canasta", "templates", "argocd-application.yaml")
REPO = "git@github.com:example/wiki-gitops.git"


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _names(tasks):
    return [t.get("name") for t in tasks]


def _render(expr, variables):
    from ansible.parsing.dataloader import DataLoader
    from ansible.template import Templar, trust_as_template

    def trust(v):
        if isinstance(v, dict):
            return {k: trust(x) for k, x in v.items()}
        if isinstance(v, list):
            return [trust(x) for x in v]
        return trust_as_template(v) if isinstance(v, str) else v
    templar = Templar(loader=DataLoader(), variables=variables)
    return templar.template(trust(expr))


class TestNewApplications:
    def test_init_and_join_name_the_instance_project(self):
        for path in (INIT, JOIN, CHART_APP):
            with open(path) as f:
                text = f.read()
            assert "project: default" not in text, path
        for path in (INIT, JOIN):
            with open(path) as f:
                assert "project: canasta-{{ instance_id }}" in f.read(), path

    def test_project_is_applied_before_the_application(self):
        tasks = _load(APPLY_APP)
        names = _names(tasks)
        proj = names.index("Apply the instance's Argo CD AppProject")
        app = names.index("Apply Argo CD Application to cluster")
        assert proj < app
        inc = tasks[proj]
        assert inc["ansible.builtin.include_tasks"] == "_apply_argo_project.yml"
        assert "when" not in inc
        assert _render(inc["vars"]["_argo_project_repo"], {
            "_argo_app": {"spec": {"source": {"repoURL": REPO}}}}) == REPO

    def test_project_admits_only_the_instance_repo_and_namespace(self):
        task = _load(APPLY_PROJECT)[0]["kubernetes.core.k8s"]
        proj = _render(task["definition"], {
            "instance_id": "wiki1", "_argo_project_repo": REPO})
        assert proj["kind"] == "AppProject"
        assert proj["metadata"]["name"] == "canasta-wiki1"
        assert proj["metadata"]["namespace"] == "argocd"
        spec = proj["spec"]
        assert spec["sourceRepos"] == [REPO]
        assert spec["destinations"] == [{
            "server": "https://kubernetes.default.svc",
            "namespace": "canasta-wiki1"}]
        # Omitted means no cluster-scoped resource is permitted.
        assert "clusterResourceWhitelist" not in spec


class TestDelete:
    def test_project_removed_after_the_application(self):
        tasks = _load(DELETE)
        kinds = [t["kubernetes.core.k8s"]["kind"] for t in tasks]
        assert kinds.index("Application") < kinds.index("AppProject")
        proj = tasks[kinds.index("AppProject")]
        assert proj["kubernetes.core.k8s"]["state"] == "absent"
        assert proj["kubernetes.core.k8s"]["name"] == "canasta-{{ instance_id }}"
        assert proj["when"] == tasks[kinds.index("Application")]["when"]


class TestUpgradeHook:
    def test_upgrade_migrates_only_stage_only_instances(self):
        tasks = _load(UPGRADE)
        names = _names(tasks)
        i = names.index("Move the Argo CD Application into the instance's own project")
        assert i > names.index(
            "Decide whether the upgrade only stages the instance for a gitops push")
        assert i < names.index("Leave the deploy of a gitops-managed instance to Argo CD")
        assert "migrate_argo_project.yml" in tasks[i]["ansible.builtin.include_tasks"]
        assert tasks[i]["when"] == "_upgrade_stage_only | bool"


MANIFEST = """apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: canasta-inst
  namespace: argocd
spec:
  project: default
  source:
    repoURL: %s
""" % REPO


def _stubbed_migration():
    """The migration tasks with every cluster call replaced by a recorder."""
    tasks = _load(MIGRATE)
    for t in tasks:
        if "kubernetes.core.k8s_info" in t:
            t.pop("kubernetes.core.k8s_info")
            t.pop("register")
            t["ansible.builtin.set_fact"] = {"_map_app": "{{ _test_live }}"}
        elif "kubernetes.core.k8s" in t:
            k8s = t.pop("kubernetes.core.k8s")
            t["vars"] = {"_test_project": k8s["definition"]["spec"]["project"]}
            t["ansible.builtin.set_fact"] = {
                "_test_calls": "{{ _test_calls + [{'patch': _test_project}] }}"}
        elif "ansible.builtin.include_tasks" in t:
            t.pop("ansible.builtin.include_tasks")
            t["ansible.builtin.set_fact"] = {
                "_test_calls": "{{ _test_calls + [{'project': _argo_project_repo}] }}"}
    return tasks


def _migrate(tmp_path, live):
    inst = tmp_path / "inst"
    (inst / "argocd").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=inst, check=True)
    (inst / "argocd" / "application.yaml").write_text(MANIFEST)
    result = tmp_path / "result.json"
    play = [{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {
            "ansible_python_interpreter": sys.executable,
            "canasta_root": REPO_ROOT,
            "instance_path": str(inst),
            "instance_id": "inst",
            "_test_calls": [],
            "_test_live": {"resources": live},
        },
        "tasks": _stubbed_migration() + [{
            "ansible.builtin.copy": {
                "content": "{{ {'calls': _test_calls} | to_json }}",
                "dest": str(result),
                "mode": "0644",
            },
        }],
    }]
    playfile = tmp_path / "play.yml"
    playfile.write_text(yaml.safe_dump(play, sort_keys=False))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0")
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(playfile)],
                          capture_output=True, text=True, env=env, cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    staged = subprocess.run(["git", "diff", "--cached", "--name-only"], cwd=inst,
                            capture_output=True, text=True, check=True).stdout
    return (proc.stdout, json.loads(result.read_text())["calls"],
            (inst / "argocd" / "application.yaml").read_text(), staged)


def _app(project="default", namespace="canasta-inst",
         server="https://kubernetes.default.svc"):
    return [{"spec": {"project": project,
                      "source": {"repoURL": REPO},
                      "destination": {"server": server, "namespace": namespace}}}]


class TestMigration:
    def test_default_project_moves_to_the_instance_project(self, tmp_path):
        out, calls, manifest, staged = _migrate(tmp_path, _app())
        # The project exists, admitting the live repo, before the patch.
        assert calls == [{"project": REPO},
                         {"patch": "canasta-inst"}]
        assert "project: canasta-inst\n" in manifest
        assert "project: default" not in manifest
        assert staged.split() == ["argocd/application.yaml"]
        assert "Moved Argo CD Application 'canasta-inst'" in out

    def test_already_moved_is_left_alone(self, tmp_path):
        _, calls, manifest, staged = _migrate(tmp_path, _app("canasta-inst"))
        assert calls == []
        assert staged == ""

    def test_operator_chosen_project_is_left_alone(self, tmp_path):
        _, calls, _, staged = _migrate(tmp_path, _app("team-wikis"))
        assert calls == []
        assert staged == ""

    def test_unexpected_destination_is_left_with_a_warning(self, tmp_path):
        out, calls, _, staged = _migrate(tmp_path, _app(namespace="other"))
        assert calls == []
        assert staged == ""
        assert "WARNING: Argo CD Application 'canasta-inst' is in the" in out

    def test_missing_application_is_a_no_op(self, tmp_path):
        out, calls, _, staged = _migrate(tmp_path, [])
        assert calls == []
        assert staged == ""
        assert "WARNING" not in out
