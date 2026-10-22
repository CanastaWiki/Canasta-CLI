"""The image reclaim on a host whose instances run under Podman.

Probing for Docker alone found no runtime on such a host, so `image prune`
and `upgrade --purge` reclaimed nothing and superseded images piled up
until a pull ran out of disk.

Runs the real image_reclaim.yml against a stubbed `podman`, a `docker` and
`kubectl` that are absent, and a throwaway registry.
"""

import json
import os
import stat
import subprocess
import sys

import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
RECLAIM = os.path.join(
    REPO_ROOT, "roles", "orchestrator", "tasks", "image_reclaim.yml")
COMMON = os.path.join(REPO_ROOT, "roles", "common")

PINNED = "ghcr.io/canastawiki/canasta:3.5.29"
SUPERSEDED = "ghcr.io/canastawiki/canasta:3.5.28"

PODMAN = r"""#!/bin/sh
echo "$*" >> "$STUB_LOG"
case "$*" in
  "info "*) echo 5.4.2 ;;
  "images ghcr.io/canastawiki/canasta "*) printf '%s\n' $STUB_CANASTA_IMAGES ;;
  "image rm "*) echo "Untagged: $3" ;;
  "image prune -f") printf '%s\n' $STUB_DANGLING ;;
  *) echo "unexpected: $*" >&2; exit 2 ;;
esac
"""


def _stub(bin_dir, name, body):
    path = bin_dir / name
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _run(tmp_path, dry_run=False, dangling=""):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub(bin_dir, "podman", PODMAN)
    _stub(bin_dir, "docker", "#!/bin/sh\nexit 127\n")
    _stub(bin_dir, "kubectl", "#!/bin/sh\nexit 127\n")

    site = tmp_path / "site"
    site.mkdir()
    (site / "docker-compose.yml").write_text("services: {}\n")
    (site / ".env").write_text("CANASTA_IMAGE=%s\n" % PINNED)

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "conf.json").write_text(json.dumps({"Instances": {
        "site": {"id": "site", "path": str(site), "orchestrator": "compose",
                 "composeCommand": "podman-compose",
                 "inspectCommand": "podman"},
    }}))

    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {"canasta_root": REPO_ROOT, "_reclaim_host_name": "localhost",
                 "reclaim_dry_run": dry_run,
                 "ansible_python_interpreter": sys.executable},
        "tasks": [{"ansible.builtin.include_tasks": RECLAIM}],
    }]))
    inventory = tmp_path / "hosts"
    inventory.write_text(
        "localhost ansible_connection=local\n"
        "canasta_controller ansible_connection=local\n")
    log = tmp_path / "podman.log"
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               PATH="%s:%s" % (bin_dir, os.environ["PATH"]),
               STUB_LOG=str(log),
               STUB_CANASTA_IMAGES=" ".join([PINNED, SUPERSEDED]),
               STUB_DANGLING=dangling,
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


class TestPodmanHost:
    def test_the_host_is_not_reported_as_having_no_runtime(self, tmp_path):
        out, _ = _run(tmp_path)
        assert "No container runtime found" not in out

    def test_superseded_tag_is_removed_and_pinned_tag_kept(self, tmp_path):
        _, calls = _run(tmp_path)
        assert "image rm %s" % SUPERSEDED in calls
        assert "image rm %s" % PINNED not in calls

    def test_dangling_images_are_pruned_and_counted(self, tmp_path):
        out, calls = _run(tmp_path, dangling="1a2b3c 4d5e6f")
        assert "image prune -f" in calls
        assert ("Podman: removed 1 Canasta tag(s) and 0 unused built "
                "image(s); 2 dangling image(s) pruned") in out

    def test_dry_run_removes_nothing(self, tmp_path):
        out, calls = _run(tmp_path, dry_run=True)
        assert not [c for c in calls if c.startswith("image rm")]
        assert "image prune -f" not in calls
        assert "Podman: would remove 1 Canasta" in out

    def test_compose_built_images_are_left_alone(self, tmp_path):
        _, calls = _run(tmp_path)
        assert not [c for c in calls if "--filter" in c]
