"""create finds and removes exactly its own Compose volumes.

The stale-volume check must match the instance's Compose project, not a
volume-name substring, and the rescue's `down -v` must name the active
profiles on podman-compose, which ignores COMPOSE_PROFILES. Both run here
against stub runtimes.
"""

import os
import subprocess
import sys

import yaml

from _orchestrator import INSTANCE_IS_K8S_VARS

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
VALIDATE = os.path.join(REPO_ROOT, "roles", "create", "tasks",
                        "_validate_inputs.yml")
CREATE = os.path.join(REPO_ROOT, "playbooks", "create.yml")
COMMON = os.path.join(REPO_ROOT, "roles", "common")

# Volumes are listed as "<compose project> <volume name>" in $STUB_VOLUMES.
DOCKER_STUB = r"""#!/bin/bash
[ "$1 $2" = "volume ls" ] || exit 0
filter="$4"
while read -r project name; do
  case "$filter" in
    label=com.docker.compose.project=*)
      [ "$project" = "${filter#label=com.docker.compose.project=}" ] && echo "$name" ;;
    name=*)
      case "$name" in *"${filter#name=}"*) echo "$name" ;; esac ;;
  esac
done < "$STUB_VOLUMES"
"""

COMPOSE_STUB = r"""#!/bin/bash
echo "$*" >> "$STUB_LOG"
"""


def _run(tmp_path, tasks, extra_vars, env_extra=None):
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": dict(INSTANCE_IS_K8S_VARS, **extra_vars,
                     ansible_python_interpreter=sys.executable),
        "tasks": tasks,
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_LIBRARY=os.path.join(COMMON, "library"),
               ANSIBLE_MODULE_UTILS=os.path.join(COMMON, "module_utils"),
               **(env_extra or {}))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    return subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env,
                          cwd=tmp_path)


def _stub(tmp_path, name, body):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    path = bin_dir / name
    path.write_text(body)
    path.chmod(0o755)
    return str(path)


class TestStaleVolumeCheck:
    def _check(self, tmp_path, instance_id, volumes):
        with open(VALIDATE) as f:
            tasks = yaml.safe_load(f)
        block = next(t for t in tasks
                     if "stale Compose volumes" in str(t.get("name")))
        vol_file = tmp_path / "volumes"
        vol_file.write_text("".join("%s %s\n" % v for v in volumes))
        stub = _stub(tmp_path, "docker", DOCKER_STUB)
        return _run(tmp_path, [block],
                    {"id": instance_id, "inspect_command": stub},
                    {"STUB_VOLUMES": str(vol_file)})

    def test_another_instance_whose_name_ends_the_same_is_ignored(self, tmp_path):
        proc = self._check(tmp_path, "wiki", [
            ("mainwiki", "mainwiki_mysql-data-volume")])
        assert proc.returncode == 0, proc.stdout + proc.stderr

    def test_own_volumes_are_found_for_a_mixed_case_id(self, tmp_path):
        proc = self._check(tmp_path, "MyWiki", [
            ("mywiki", "mywiki_mysql-data-volume")])
        assert proc.returncode != 0
        assert "mywiki_mysql-data-volume" in proc.stdout + proc.stderr


class TestRescueDown:
    def _rescue(self, tmp_path, compose_name, profiles):
        with open(CREATE) as f:
            doc = yaml.safe_load(f)
        rescue = next(t for t in doc if isinstance(t, dict) and "rescue" in t)
        names = ("Read COMPOSE_PROFILES", "Build compose profile flags",
                 "destroy Compose containers")
        tasks = [t for t in rescue["rescue"]
                 if any(n in str(t.get("name")) for n in names)]
        assert len(tasks) == 3
        inst = tmp_path / "inst"
        inst.mkdir()
        (inst / ".env").write_text("COMPOSE_PROFILES=%s\n" % profiles)
        log = tmp_path / "compose.log"
        stub = _stub(tmp_path, compose_name, COMPOSE_STUB)
        proc = _run(tmp_path, tasks,
                    {"instance_path": str(inst), "compose_command": stub},
                    {"STUB_LOG": str(log)})
        assert proc.returncode == 0, proc.stdout + proc.stderr
        return log.read_text().strip()

    def test_podman_down_names_each_profile(self, tmp_path):
        call = self._rescue(tmp_path, "podman-compose",
                            "web,internal-db, caddy")
        assert call == ("--profile web --profile internal-db "
                        "--profile caddy down -v")

    def test_docker_down_relies_on_compose_profiles(self, tmp_path):
        call = self._rescue(tmp_path, "docker-compose", "web,internal-db")
        assert call == "down -v"
