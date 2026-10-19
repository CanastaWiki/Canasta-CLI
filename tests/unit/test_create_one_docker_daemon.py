"""create refuses an instance on a second Docker daemon.

Rootful and rootless Docker can be installed side by side, but a host's
instances share one HAProxy edge on one daemon, so an instance on the other
daemon could not join it.
"""
import os
import stat
import subprocess

import jinja2
import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PREFLIGHT = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks", "create_preflight.yml")


def _walk(tasks):
    for t in tasks or []:
        yield t
        for k in ("block", "rescue", "always"):
            yield from _walk(t.get(k))


def _task(name):
    with open(PREFLIGHT) as fh:
        return next(t for t in _walk(yaml.safe_load(fh)) if t.get("name") == name)


def _probe(tmp_path, instance_host, edges):
    """Run the probe with a fake docker. edges maps socket path -> (daemon
    id, whether that daemon holds canasta-edge)."""
    xdg = tmp_path / "xdg"
    xdg.mkdir()
    rootless = str(xdg / "docker.sock")
    daemons = {"/var/run/docker.sock": edges.get("rootful"),
               rootless: edges.get("rootless")}
    case = "\n".join(
        '  "unix://%s") id=%s; edge=%s ;;' % (sock, d[0], "1" if d[1] else "")
        for sock, d in daemons.items() if d)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "docker"
    fake.write_text(
        "#!/bin/sh\n[ \"$1\" = -H ] || exit 1\ncase \"$2\" in\n%s\n"
        "  *) exit 1 ;;\nesac\n"
        "[ \"$3\" = info ] && { echo \"$id\"; exit 0; }\n"
        "[ \"$3\" = ps ] && [ -n \"$edge\" ] && echo abc123\nexit 0\n" % case)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    cmd = _task("Look for an edge on the other Docker daemon")[
        "ansible.builtin.shell"]["cmd"]
    env = jinja2.Environment()
    env.filters["quote"] = lambda v: "'%s'" % v
    script = env.from_string(cmd).render(_preflight_docker_host=instance_host)
    # The probe tests the sockets with -S; stand in plain paths for them.
    script = script.replace('[ -S "$s" ]', '[ -n "$s" ]')
    out = subprocess.run(
        ["sh", "-c", script], capture_output=True, text=True, check=True,
        env=dict(os.environ, PATH="%s:%s" % (bindir, os.environ["PATH"]),
                 XDG_RUNTIME_DIR=str(xdg)))
    return out.stdout.strip(), rootless


def test_an_edge_on_the_rootless_daemon_is_found_for_a_rootful_instance(tmp_path):
    found, rootless = _probe(tmp_path, "", {"rootful": ("A", False),
                                            "rootless": ("B", True)})
    assert found == rootless


def test_an_edge_on_the_rootful_daemon_is_found_for_a_rootless_instance(tmp_path):
    xdg_sock = str(tmp_path / "xdg" / "docker.sock")
    found, _ = _probe(tmp_path, "unix://" + xdg_sock,
                      {"rootful": ("A", True), "rootless": ("B", False)})
    assert found == "/var/run/docker.sock"


def test_the_instances_own_daemon_is_not_a_second_one(tmp_path):
    found, _ = _probe(tmp_path, "", {"rootful": ("A", True),
                                     "rootless": ("B", False)})
    assert found == ""


def test_two_paths_to_one_daemon_are_one_daemon(tmp_path):
    found, _ = _probe(tmp_path, "", {"rootful": ("A", True),
                                     "rootless": ("A", True)})
    assert found == ""


def test_the_refusal_names_both_runtimes_and_runs_only_when_found():
    task = _task("Refuse a second Docker daemon on the host")
    msg = task["ansible.builtin.fail"]["msg"]
    assert "rootful" in msg and "rootless" in msg and "--docker-host" in msg
    assert "_preflight_other_docker_edge.stdout" in str(task["when"])


def test_podman_instances_skip_the_probe():
    task = _task("Look for an edge on the other Docker daemon")
    assert "podman" in str(task["when"])
