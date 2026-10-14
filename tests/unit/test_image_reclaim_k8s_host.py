"""The Docker reclaim on a host that also runs Kubernetes instances.

A Kubernetes instance has no compose project, so reading one fails. That
must not stop the reclaim of a Compose instance's unused built images.
The Docker copies of images built for the in-cluster registry, and the
build cache, are kept and reported with the command that removes them.

Runs the real image_reclaim.yml against a stubbed `docker` and `kubectl`
and a throwaway registry.
"""

import json
import os
import stat
import subprocess
import sys

import yaml

from direct_commands.doctor import _reclaimable_line

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
RECLAIM = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "image_reclaim.yml")
COMMON = os.path.join(REPO_ROOT, "roles", "common")

K8S_COPIES = [
    "localhost:5000/canasta:local",
    "canasta:local",
    "canasta-base:local",
    "localhost:5000/canasta-kube-search:local",
]

# A stand-in for the docker CLI. `compose config` succeeds only in a
# directory holding a compose file, as the real one does.
DOCKER = r"""#!/bin/sh
echo "$*" >> "$STUB_LOG"
case "$*" in
  "info "*) echo 27.0.0 ;;
  "images ghcr.io/canastawiki/canasta "*) echo ghcr.io/canastawiki/canasta:3.5.0 ;;
  "images --filter label=com.docker.compose.project=site "*) echo site-web:custom ;;
  "images --filter "*) ;;
  "images --format "*) printf '%s\n' $STUB_ALL_IMAGES ;;
  "compose "*"config --images")
    [ -f docker-compose.yml ] || { echo 'no configuration file' >&2; exit 1; }
    echo ghcr.io/canastawiki/canasta:3.5.0 ;;
  "image rm "*) echo "Untagged: $3" ;;
  "image prune -f") echo 'Total reclaimed space: 0B' ;;
  "system df "*) printf 'Images|10.09GB (95%%)\nBuild Cache|6.3GB\n' ;;
  *) echo "unexpected: $*" >&2; exit 2 ;;
esac
"""


def _stub(bin_dir, name, body):
    path = bin_dir / name
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _run(tmp_path, k8s_pin="canasta:local", compose_pin=None):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub(bin_dir, "docker", DOCKER)
    _stub(bin_dir, "kubectl", "#!/bin/sh\nexit 1\n")

    site = tmp_path / "site"
    site.mkdir()
    (site / "docker-compose.yml").write_text("services: {}\n")
    (site / ".env").write_text(
        "CANASTA_IMAGE=%s\n" % (compose_pin or "ghcr.io/canastawiki/canasta:3.5.0"))
    kube = tmp_path / "kube"
    kube.mkdir()
    (kube / ".env").write_text("CANASTA_IMAGE=%s\n" % k8s_pin)

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "conf.json").write_text(json.dumps({"Instances": {
        "site": {"id": "site", "path": str(site), "orchestrator": "compose"},
        "kube": {"id": "kube", "path": str(kube), "orchestrator": "kubernetes"},
    }}))

    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {"canasta_root": REPO_ROOT, "_reclaim_host_name": "localhost",
                 "ansible_python_interpreter": sys.executable},
        "tasks": [{"ansible.builtin.include_tasks": RECLAIM}],
    }]))
    inventory = tmp_path / "hosts"
    inventory.write_text(
        "localhost ansible_connection=local\n"
        "canasta_controller ansible_connection=local\n")
    log = tmp_path / "docker.log"
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               PATH="%s:%s" % (bin_dir, os.environ["PATH"]),
               STUB_LOG=str(log),
               STUB_ALL_IMAGES=" ".join(
                   K8S_COPIES + ["site-web:custom", "redis:7.4-alpine"]),
               CANASTA_CONFIG_DIR=str(config_dir),
               ANSIBLE_FILTER_PLUGINS=os.path.join(REPO_ROOT, "filter_plugins"),
               ANSIBLE_LIBRARY=os.path.join(COMMON, "library"),
               ANSIBLE_MODULE_UTILS=os.path.join(COMMON, "module_utils"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", str(inventory), str(play)],
                          capture_output=True, text=True, env=env,
                          cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    calls = log.read_text().splitlines() if log.exists() else []
    return proc.stdout, calls


class TestKubernetesInstanceOnTheHost:
    def test_compose_built_images_are_still_reclaimed(self, tmp_path):
        out, calls = _run(tmp_path)
        assert "image rm site-web:custom" in calls
        assert "could not be read" not in out
        assert "1 unused built image(s)" in out

    def test_kubernetes_instance_is_not_read_as_a_compose_project(self, tmp_path):
        out, calls = _run(tmp_path)
        compose_reads = [c for c in calls if c.startswith("compose ")]
        assert len(compose_reads) == 1

    def test_kubernetes_build_copies_are_kept_and_reported(self, tmp_path):
        out, calls = _run(tmp_path)
        for ref in K8S_COPIES:
            assert "image rm %s" % ref not in calls
        assert "kept 4 image(s) built for the in-cluster registry" in out
        assert "docker image rm %s'" % " ".join(sorted(K8S_COPIES)) in out

    def test_a_copy_a_compose_instance_runs_is_not_offered(self, tmp_path):
        out, _ = _run(tmp_path, compose_pin="canasta:local")
        assert "kept 3 image(s)" in out
        assert ("docker image rm canasta-base:local "
                "localhost:5000/canasta-kube-search:local "
                "localhost:5000/canasta:local'") in out

    def test_build_cache_is_reported_with_its_prune_command(self, tmp_path):
        out, calls = _run(tmp_path)
        assert not any(c.startswith("builder ") for c in calls)
        assert ("Docker build cache: 6.3GB reclaimable, kept to speed up "
                "rebuilds; reclaim it with 'docker builder prune'.") in out


K3S = "k3s version v1.31.5+k3s1"
DF = "Images|10.09GB (95%)\nBuild Cache|6.3GB"


class TestDoctorAdvice:
    def test_build_cache_points_at_docker_builder_prune(self):
        line = _reclaimable_line(DF, "MISSING")
        assert ("reclaim images with 'canasta image prune', build cache "
                "with 'docker builder prune'") in line

    def test_build_cache_alone_does_not_name_image_prune(self):
        line = _reclaimable_line("Images|0B (0%)\nBuild Cache|6.3GB", "MISSING")
        assert "canasta image prune" not in line
        assert "docker builder prune" in line

    def test_cluster_host_says_image_prune_keeps_the_kubernetes_copies(self):
        line = _reclaimable_line(DF, K3S)
        assert "keeps, the Docker copies of images built for Kubernetes" in line
        assert "docker builder prune" in line
