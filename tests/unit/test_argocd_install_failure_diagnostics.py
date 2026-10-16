"""A failed Argo CD install reports which pod was not ready and why.

`helm upgrade --install --wait` fails with only "context deadline
exceeded"; the bootstrap now prints the namespace's pods, the waiting
reason of each container that is not ready, recent Warning events and the
images the release asked for, then fails with Helm's error.
"""

import os
import stat
import subprocess
import sys

import pytest
import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TASKS = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks")
BOOTSTRAP = os.path.join(TASKS, "k8s_argocd_bootstrap.yml")
CAPTURE = os.path.join(TASKS, "k8s_capture_argocd_install_failure.yml")
TOOL_VERSIONS = os.path.join(REPO_ROOT, "vars", "tool_versions.yml")

FAKE_HELM_REPOSITORY = """#!/usr/bin/python
from ansible.module_utils.basic import AnsibleModule
m = AnsibleModule(argument_spec=dict(name=dict(), repo_url=dict(),
                                       force_update=dict(type="bool")))
m.exit_json(changed=False)
"""


def _fake(bindir, name, body):
    path = bindir / name
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _run(tmp_path, helm_upgrade_rc):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "calls.log"
    template = tmp_path / "template.txt"
    values = tmp_path / "values.yaml"
    _fake(bindir, "helm",
          'echo "helm $*" >> %s\n' % log
          + 'case "$1 $2" in\n'
          '  "upgrade --install")\n'
          '    prev=""; for a in "$@"; do\n'
          '      [ "$prev" = "-f" ] && cat "$a" >> ' + str(values) + '\n'
          '      prev="$a"\n'
          '    done\n'
          + ('    echo "Error: context deadline exceeded" >&2; exit 1 ;;\n'
             if helm_upgrade_rc else '    exit 0 ;;\n')
          + '  "list -n") echo "argocd  argocd  1  failed  argo-cd-9.0.0  v3.1.0" ;;\n'
          '  "get manifest")\n'
          '    echo "kind: Secret"\n'
          '    echo "data:"\n'
          '    echo "  server.secretkey: c2VjcmV0LXZhbHVl"\n'
          '    echo "        image: quay.io/argoproj/argocd:v3.1.0"\n'
          '    echo "        image: ecr-public.aws.com/docker/library/redis:7"\n'
          '    ;;\n'
          'esac\n'
          'exit 0\n')
    _fake(bindir, "kubectl",
          'echo "kubectl $*" >> %s\n' % log
          + 'case "$*" in\n'
          '  "get deployment argocd-server"*)\n'
          '    echo "Error from server (NotFound)" >&2; exit 1 ;;\n'
          '  *"-o go-template="*)\n'
          '    for a in "$@"; do\n'
          '      case "$a" in -o) ;; go-template=*) printf "%s" "${a#go-template=}" > ' + str(template) + ' ;; esac\n'
          '    done\n'
          '    echo "argocd-redis-abc/redis image=ecr-public.aws.com/docker/library/redis:7'
          ' restarts=0 waiting=ImagePullBackOff: Back-off pulling image"\n'
          '    ;;\n'
          '  "get pods -n argocd --no-headers")\n'
          '    echo "argocd-repo-server-x 0/1 Pending 0 10m" ;;\n'
          '  "get pods -n argocd -o wide")\n'
          '    echo "argocd-redis-abc 0/1 ImagePullBackOff 0 10m" ;;\n'
          '  "get events"*)\n'
          '    echo "2m Warning Failed pod/argocd-redis-abc Failed to pull image" ;;\n'
          '  "describe nodes")\n'
          '    echo "Name:               runner"\n'
          '    echo "  MemoryPressure   False"\n'
          '    echo "  memory:             16Gi"\n'
          '    echo "  Hostname:  runner"\n'
          '    ;;\n'
          'esac\n'
          'exit 0\n')
    # The bootstrap names kubernetes.core.helm_repository and CI has no
    # kubernetes.core.
    mods = tmp_path / "ac" / "ansible_collections" / "kubernetes" / "core" \
        / "plugins" / "modules"
    mods.mkdir(parents=True)
    (mods / "helm_repository.py").write_text(FAKE_HELM_REPOSITORY)
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars_files": [TOOL_VERSIONS],
        "vars": {
            "canasta_root": REPO_ROOT,
            "rx_delay": 1,
            "ansible_python_interpreter": sys.executable,
        },
        "tasks": [{"ansible.builtin.include_tasks": BOOTSTRAP}],
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               PATH=str(bindir) + os.pathsep + os.environ["PATH"],
               ANSIBLE_COLLECTIONS_PATH=str(tmp_path / "ac"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env,
                          cwd=tmp_path)
    calls = log.read_text().splitlines() if log.exists() else []
    tmpl = template.read_text() if template.exists() else ""
    vals = values.read_text() if values.exists() else ""
    return proc, calls, tmpl, vals


@pytest.fixture(autouse=True)
def _posix_only():
    if os.name != "posix":
        pytest.skip("needs a POSIX shell for the fake binaries")


class TestFailedInstall:
    def test_reports_cluster_state_and_fails_with_helms_error(self, tmp_path):
        proc, calls, _, _ = _run(tmp_path, helm_upgrade_rc=1)
        out = proc.stdout + proc.stderr
        assert proc.returncode != 0, out
        assert "Argo CD did not become ready" in out
        assert "waiting=ImagePullBackOff" in out
        assert "Failed to pull image" in out
        assert "argocd-9.0.0" in out or "argo-cd-9.0.0" in out
        assert "image: quay.io/argoproj/argocd:v3.1.0" in out
        # Pods are Pending, so node capacity is reported.
        assert "nodes (pods are Pending)" in out
        assert "memory:             16Gi" in out
        assert "context deadline exceeded" in out
        # The diagnostic ran after the install failed, not before.
        first_upgrade = next(i for i, c in enumerate(calls)
                             if c.startswith("helm upgrade --install"))
        first_probe = next(i for i, c in enumerate(calls)
                           if c.startswith("helm list"))
        assert first_upgrade < first_probe
        assert any("--field-selector type=Warning" in c for c in calls), calls

    def test_never_prints_secret_data_from_the_manifest(self, tmp_path):
        proc, _, _, _ = _run(tmp_path, helm_upgrade_rc=1)
        out = proc.stdout + proc.stderr
        assert "c2VjcmV0LXZhbHVl" not in out

    def test_go_template_reaches_kubectl_unrendered_by_jinja(self, tmp_path):
        _, _, tmpl, _ = _run(tmp_path, helm_upgrade_rc=1)
        assert tmpl.startswith("{{range .items}}{{$p := .metadata.name}}")
        assert "{{with .state.waiting}} waiting={{.reason}}" in tmpl
        assert tmpl.count("{{range ") + tmpl.count("{{if ") \
            + tmpl.count("{{with ") == tmpl.count("{{end}}")


class TestSuccessfulInstall:
    def test_no_diagnostic_when_the_install_succeeds(self, tmp_path):
        proc, calls, _, _ = _run(tmp_path, helm_upgrade_rc=0)
        out = proc.stdout + proc.stderr
        assert proc.returncode == 0, out
        assert "Argo CD did not become ready" not in out
        assert not any(c.startswith("helm list") for c in calls), calls


class TestPinnedInstall:
    def test_installs_the_pinned_chart_version(self, tmp_path):
        proc, calls, _, _ = _run(tmp_path, helm_upgrade_rc=0)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        pinned = yaml.safe_load(open(TOOL_VERSIONS))["argocd_chart_version"]
        install = [c for c in calls if c.startswith("helm upgrade --install")]
        assert install, calls
        assert all(("--version %s " % pinned) in c for c in install), install

    def test_pulls_redis_from_the_mirror(self, tmp_path):
        proc, _, _, vals = _run(tmp_path, helm_upgrade_rc=0)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        image = yaml.safe_load(vals)["redis"]["image"]
        assert image == {"repository": "mirror.gcr.io/library/redis",
                         "tag": "8.6.4-alpine"}


def test_sidecar_upgrade_keeps_the_installed_chart_version():
    # A cluster created before the pin must not have its Argo CD moved to
    # the pinned chart when gitops init adds the SOPS sidecar.
    ensure = open(os.path.join(TASKS, "k8s_ensure_sops_sidecar.yml")).read()
    assert "--version {{ _argocd_chart_version }}" in ensure
    assert "argocd_chart_version }}" not in ensure.replace(
        "_argocd_chart_version }}", "")


def test_capture_is_best_effort():
    tasks = yaml.safe_load(open(CAPTURE))
    probe = next(t for t in tasks if "ansible.builtin.shell" in t)
    assert probe["failed_when"] is False
