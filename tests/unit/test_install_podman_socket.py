"""`install podman` must provision the socket --docker-host points at.

The task file configured two rootless prerequisites — lingering and the
unprivileged port floor — and reported "Rootless prerequisites were
configured". The socket was not one of them, so:

    $ canasta install -H host podman
    Podman already available. Rootless prerequisites were configured.
    $ ssh host 'systemctl --user is-active podman.socket'
    inactive

while --docker-host on both `create` and `install` documents
`unix:///run/user/<uid>/podman/podman.sock`. The normal podman path never
touches the socket — it shells out to podman-compose and podman — so this
only bites the operator who follows that documented route.
"""

import os

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
PODMAN = os.path.join(REPO_ROOT, "roles", "install", "tasks", "podman.yml")


def _all_tasks():
    with open(PODMAN) as f:
        doc = yaml.safe_load(f)
    out = []
    for task in doc:
        out.append(task)
        out.extend(task.get("block") or [])
    return out


def _named(name):
    for t in _all_tasks():
        if t.get("name") == name:
            return t
    raise AssertionError("task not found: %s" % name)


class TestTheSocketIsEnabled:
    def test_the_socket_task_exists(self):
        task = _named("Enable the rootless Podman socket for the operator")
        cmd = task["ansible.builtin.command"]["cmd"]
        assert "systemctl --user" in cmd
        assert "podman.socket" in cmd
        assert "--now" in cmd, (
            "enable without --now leaves the socket inactive until reboot"
        )

    def test_it_runs_as_the_operator_not_root(self):
        # A user unit: as root this would enable root's socket, which is
        # not the one --docker-host names.
        task = _named("Enable the rootless Podman socket for the operator")
        assert task["become"] is False

    def test_it_supplies_the_runtime_dir(self):
        # systemctl --user needs XDG_RUNTIME_DIR, which a non-interactive
        # SSH session does not set.
        task = _named("Enable the rootless Podman socket for the operator")
        env = task["environment"]
        assert "XDG_RUNTIME_DIR" in env
        assert "_install_uid" in env["XDG_RUNTIME_DIR"]

    def test_failure_is_not_fatal(self):
        # Rootless podman works through the CLI without the socket, so a
        # host that cannot enable it must still finish the install.
        task = _named("Enable the rootless Podman socket for the operator")
        assert task["failed_when"] is False

    def test_a_failure_is_reported(self):
        task = _named("Warn if the Podman socket could not be enabled")
        msg = task["ansible.builtin.debug"]["msg"]
        assert "podman.socket" in msg
        assert "docker-host" in msg


class TestTheOperatorUidIsResolved:
    def test_uid_is_read_as_the_operator(self):
        task = _named("Determine operator uid")
        assert task["ansible.builtin.command"] == "id -u"
        assert task["become"] is False, (
            "root's uid would point XDG_RUNTIME_DIR at the wrong directory"
        )
