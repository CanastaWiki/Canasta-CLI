"""canasta install docker-rootless sets up rootless Docker for the operator,
alongside rootful Docker, configured so the HAProxy edge receives visitors'
IP addresses."""
import json
import os

import jinja2.nativetypes
import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TASKS = os.path.join(REPO_ROOT, "roles", "install", "tasks", "docker_rootless.yml")
PLAYBOOK = os.path.join(REPO_ROOT, "playbooks", "install.yml")
DEFS = os.path.join(REPO_ROOT, "meta", "command_definitions.yml")


def _walk(tasks):
    for t in tasks or []:
        yield t
        for k in ("block", "rescue", "always"):
            yield from _walk(t.get(k))


def _tasks(path=TASKS):
    with open(path) as fh:
        return list(_walk(yaml.safe_load(fh)))


def _names(path=TASKS):
    return [t.get("name") for t in _tasks(path)]


def _named(name, path=TASKS):
    return next(t for t in _tasks(path) if t.get("name") == name)


class TestItIsAPackage:
    def test_the_playbook_accepts_and_dispatches_it(self):
        with open(PLAYBOOK) as fh:
            body = fh.read()
        assert "'docker-rootless'" in body
        task = _named("Install rootless Docker for the operator", PLAYBOOK)
        assert task["ansible.builtin.include_role"]["tasks_from"] == "docker_rootless.yml"

    def test_argparse_accepts_it(self):
        with open(DEFS) as fh:
            install = next(c for c in yaml.safe_load(fh)["commands"]
                           if c["name"] == "install")
        packages = next(p for p in install["parameters"] if p["name"] == "packages")
        assert "docker-rootless" in packages["choices"]


class TestItConfiguresTheHost:
    def test_root_is_refused(self):
        assert "_rd_uid == '0'" in str(
            _named("Refuse to install rootless Docker for root")["when"])

    def test_docker_and_the_rootless_packages_come_first(self):
        names = _names()
        assert names.index("Install Docker") < names.index(
            "Install the rootless Docker packages (Debian/Ubuntu)")
        apt = _named("Install the rootless Docker packages (Debian/Ubuntu)")
        assert "docker-ce-rootless-extras" in apt["ansible.builtin.apt"]["name"]

    def test_lingering_port_floor_and_br_netfilter_persist(self):
        floor = _named("Keep the port floor across reboots")["ansible.builtin.copy"]
        assert floor["dest"] == "/etc/sysctl.d/canasta-privport.conf"
        module = _named("Load br_netfilter at boot")["ansible.builtin.copy"]
        assert module["dest"].startswith("/etc/modules-load.d/")
        assert "br_netfilter" in module["content"]
        assert "enable-linger" in _named(
            "Enable lingering for the operator")["ansible.builtin.command"]["cmd"]


class TestTheDaemonKeepsVisitorsAddresses:
    def test_the_cli_context_goes_back_to_the_system_daemon(self):
        names = _names()
        assert names.index("Run the rootless Docker setup tool") < names.index(
            "Keep the docker CLI on the system daemon")
        assert _named("Keep the docker CLI on the system daemon")[
            "ansible.builtin.command"]["cmd"] == "docker context use default"

    def _render(self, existing):
        task = _named("Turn off the userland proxy")
        env = jinja2.nativetypes.NativeEnvironment()
        env.filters["b64decode"] = lambda v: v
        env.filters["from_json"] = json.loads
        env.filters["to_nice_json"] = lambda v: json.dumps(v, indent=4)
        env.filters["combine"] = lambda a, b: {**a, **b}
        cfg = env.from_string(task["vars"]["_rd_config"]).render(
            _rd_daemon_json={"content": existing})
        content = env.from_string(task["ansible.builtin.copy"]["content"]).render(
            _rd_config=cfg)
        return json.loads(content)

    def test_the_userland_proxy_is_turned_off(self):
        assert self._render("") == {"userland-proxy": False}

    def test_other_settings_are_kept(self):
        assert self._render('{"log-level": "warn", "userland-proxy": true}') == {
            "log-level": "warn", "userland-proxy": False}

    def test_a_config_change_restarts_the_daemon(self):
        task = _named("Restart the rootless daemon on its new configuration")
        assert "_rd_config_write is changed" in str(task["when"])

    def test_the_report_names_the_socket_for_create(self):
        msg = _named("Report rootless Docker installed")["ansible.builtin.debug"]["msg"]
        assert "--docker-host unix://{{ _rd_xdg }}/docker.sock" in msg
