"""The canasta_boot_unit module: a rootless-Podman instance comes back at boot."""

import os
import re
import shlex

import pytest
import yaml

import canasta_boot_unit as mod

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")


class FakeRunner:
    """Records systemctl calls; answers from the unit state it tracks."""

    def __init__(self, manager=True, enabled=(), enable_fails=False):
        self.manager = manager
        self.enabled = set(enabled)
        self.enable_fails = enable_fails
        self.calls = []
        self.envs = []

    def __call__(self, argv, env=None):
        self.calls.append(argv)
        self.envs.append(env or {})
        args = argv[2:]
        if args == ["show-environment"]:
            return (0 if self.manager else 1), "", ""
        if args[:2] == ["is-enabled", "--quiet"]:
            return (0 if args[2] in self.enabled else 1), "", ""
        if args[0] == "enable":
            if self.enable_fails:
                return 1, "", "Failed to enable unit"
            self.enabled.add(args[1])
        if args[0] == "disable":
            self.enabled.discard(args[1])
        return 0, "", ""

    def systemctl(self):
        return [c[2:] for c in self.calls]


@pytest.fixture
def user_units(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("PATH", "/home/op/.local/bin:/usr/bin")
    monkeypatch.setattr(mod.shutil, "which", lambda name: "/home/op/.local/bin/" + name)
    return tmp_path / "xdg" / "systemd" / "user"


def _instance(tmp_path, layers=(), env="COMPOSE_PROFILES=web,observable\n"):
    path = tmp_path / "wiki"
    path.mkdir()
    (path / "docker-compose.yml").write_text("services: {}\n")
    for layer in layers:
        (path / layer).write_text("services: {}\n")
    (path / ".env").write_text(env)
    return str(path)


def _exec_start(text):
    line = next(l for l in text.splitlines() if l.startswith("ExecStart="))
    return shlex.split(line[len("ExecStart="):])


def test_the_unit_runs_the_instance_compose_up_at_boot(tmp_path, user_units):
    path = _instance(tmp_path, layers=("docker-compose.edge.yml", "docker-compose.override.yml"))
    run = FakeRunner()
    changed, warning = mod.enable(run, "wiki", path, "podman-compose")
    assert changed and warning is None
    text = (user_units / "canasta-wiki.service").read_text()
    assert "Type=oneshot" in text and "RemainAfterExit=yes" in text
    assert "WantedBy=default.target" in text and "After=canasta-edge.service" in text
    assert "WorkingDirectory=%s" % path in text
    assert "Environment=PATH=/home/op/.local/bin:/usr/bin" in text
    assert _exec_start(text) == [
        "/home/op/.local/bin/podman-compose", "-f", "docker-compose.yml",
        "-f", "docker-compose.edge.yml", "-f", "docker-compose.override.yml",
        "--profile", "web", "--profile", "observable", "up", "-d"]
    assert ["daemon-reload"] in run.systemctl()
    assert ["enable", "canasta-wiki.service"] in run.systemctl()
    assert all(e.get("XDG_RUNTIME_DIR") for e in run.envs)


def test_the_unit_has_no_stop_command(tmp_path, user_units):
    # A shutdown stops the unit; containers then get their SIGTERM from the
    # shutdown itself, and the unit must stay enabled for the next boot.
    mod.enable(FakeRunner(), "wiki", _instance(tmp_path), "podman-compose")
    assert "ExecStop" not in (user_units / "canasta-wiki.service").read_text()


def test_devmode_adds_the_dev_layer_last(tmp_path, user_units):
    path = _instance(tmp_path, layers=("docker-compose.sidecars.yml",), env="")
    mod.enable(FakeRunner(), "wiki", path, "podman compose", devmode=True)
    argv = _exec_start((user_units / "canasta-wiki.service").read_text())
    assert argv == ["/home/op/.local/bin/podman", "compose", "-f", "docker-compose.yml",
                    "-f", "docker-compose.sidecars.yml", "-f", "docker-compose.dev.yml",
                    "up", "-d"]


def test_a_converged_unit_is_left_alone(tmp_path, user_units):
    path = _instance(tmp_path)
    mod.enable(FakeRunner(), "wiki", path, "podman-compose")
    run = FakeRunner(enabled={"canasta-wiki.service"})
    changed, _ = mod.enable(run, "wiki", path, "podman-compose")
    assert not changed
    assert run.systemctl() == [["show-environment"],
                               ["is-enabled", "--quiet", "canasta-wiki.service"]]


def test_a_profile_change_rewrites_the_unit(tmp_path, user_units):
    path = _instance(tmp_path)
    mod.enable(FakeRunner(), "wiki", path, "podman-compose")
    with open(os.path.join(path, ".env"), "w") as f:
        f.write('COMPOSE_PROFILES="web"\n')
    run = FakeRunner(enabled={"canasta-wiki.service"})
    changed, _ = mod.enable(run, "wiki", path, "podman-compose")
    assert changed and ["daemon-reload"] in run.systemctl()
    argv = _exec_start((user_units / "canasta-wiki.service").read_text())
    assert "observable" not in argv and argv[-4:] == ["--profile", "web", "up", "-d"]


def test_without_the_user_manager_it_warns_and_writes_nothing(tmp_path, user_units):
    changed, warning = mod.enable(FakeRunner(manager=False), "wiki", _instance(tmp_path),
                                  "podman-compose")
    assert not changed and "after a reboot" in warning and "lingering" in warning
    assert not user_units.exists()


def test_a_failed_enable_warns(tmp_path, user_units):
    _, warning = mod.enable(FakeRunner(enable_fails=True), "wiki", _instance(tmp_path),
                            "podman-compose")
    assert "Failed to enable unit" in warning


def test_systemd_specifiers_in_the_path_are_escaped(tmp_path, user_units):
    path = tmp_path / "100% wiki$"
    path.mkdir()
    (path / "docker-compose.yml").write_text("services: {}\n")
    mod.enable(FakeRunner(), "wiki", str(path), "podman-compose")
    text = (user_units / "canasta-wiki.service").read_text()
    assert 'WorkingDirectory="%s"' % str(path).replace("%", "%%").replace("$", "$$") in text


def test_stop_disables_but_keeps_the_unit(tmp_path, user_units):
    mod.enable(FakeRunner(), "wiki", _instance(tmp_path), "podman-compose")
    run = FakeRunner(enabled={"canasta-wiki.service"})
    assert mod.disable(run, "wiki")
    assert ["disable", "canasta-wiki.service"] in run.systemctl()
    assert (user_units / "canasta-wiki.service").exists()
    assert not mod.disable(FakeRunner(), "wiki")


def test_delete_stops_and_removes_the_unit(tmp_path, user_units):
    # A unit that ran at boot is active (RemainAfterExit); removing only its
    # file would leave it loaded as not-found/active.
    mod.enable(FakeRunner(), "wiki", _instance(tmp_path), "podman-compose")
    run = FakeRunner(enabled={"canasta-wiki.service"})
    assert mod.disable(run, "wiki", remove=True)
    assert not (user_units / "canasta-wiki.service").exists()
    assert run.systemctl() == [["disable", "--now", "canasta-wiki.service"],
                               ["daemon-reload"],
                               ["reset-failed", "canasta-wiki.service"]]


def test_stop_never_stops_the_unit(tmp_path, user_units):
    # Stopping it would kill the conmon processes in its cgroup.
    mod.enable(FakeRunner(), "wiki", _instance(tmp_path), "podman-compose")
    run = FakeRunner(enabled={"canasta-wiki.service"})
    mod.disable(run, "wiki")
    assert not any("--now" in c or c[0] == "stop" for c in run.systemctl())


def test_without_a_unit_disable_does_nothing(tmp_path, user_units):
    run = FakeRunner()
    assert not mod.disable(run, "wiki", remove=True)
    assert run.calls == []


@pytest.mark.parametrize("inspect,expected", [
    ("podman", True), ("/usr/bin/podman", True), ("docker", False), ("", False)])
def test_only_podman_uses_units(inspect, expected):
    assert mod.uses_units(inspect) is expected


# --- wiring ---------------------------------------------------------------- #

def _tasks(relpath):
    with open(os.path.join(ROOT, relpath)) as f:
        return yaml.safe_load(f)


def _boot_unit_tasks(tasks):
    found = []
    for task in tasks:
        if "canasta_boot_unit" in task:
            found.append(task)
        found += _boot_unit_tasks(task.get("block", []))
    return found


def _compose_file_args_layers():
    tasks = _tasks("roles/orchestrator/tasks/compose_file_args.yml")
    stat = next(t for t in tasks if "ansible.builtin.stat" in t)
    build = next(t for t in tasks if "ansible.builtin.set_fact" in t)
    dev = re.findall(r"'(docker-compose\.dev\.yml)'",
                     build["ansible.builtin.set_fact"]["_compose_files"])
    return ["docker-compose.yml"] + stat["loop"] + dev


def _includes_compose_file_args(relpath):
    return "include_tasks: compose_file_args.yml" in open(os.path.join(ROOT, relpath)).read()


def test_the_unit_layers_match_the_start_compose_files():
    assert _includes_compose_file_args("roles/orchestrator/tasks/start.yml")
    assert _compose_file_args_layers() == (
        ["docker-compose.yml"] + list(mod.LAYERS) + [mod.DEV_LAYER])


def test_compose_start_enables_the_unit_last():
    tasks = _tasks("roles/orchestrator/tasks/start.yml")
    compose = next(t for t in tasks if t["name"] == "Start (Docker Compose)")
    last = compose["block"][-1]
    assert last["canasta_boot_unit"]["state"] == "enabled"


def test_an_operator_stop_disables_the_unit():
    (task,) = _boot_unit_tasks(_tasks("roles/instance_lifecycle/tasks/stop.yml"))
    assert task["canasta_boot_unit"]["state"] == "disabled"
    # A stop inside restart or upgrade keeps it.
    assert not _boot_unit_tasks(_tasks("roles/orchestrator/tasks/stop.yml"))


def test_delete_removes_the_unit_wiring():
    (task,) = _boot_unit_tasks(_tasks("roles/orchestrator/tasks/destroy.yml"))
    assert task["canasta_boot_unit"]["state"] == "absent"


def test_upgrade_enables_the_unit_for_a_running_instance():
    (task,) = _boot_unit_tasks(_tasks("roles/upgrade/tasks/main.yml"))
    assert task["canasta_boot_unit"]["state"] == "enabled"
    assert "_was_running | bool" in task["when"]


def test_stop_uses_the_start_compose_files():
    assert _includes_compose_file_args("roles/orchestrator/tasks/stop.yml")
