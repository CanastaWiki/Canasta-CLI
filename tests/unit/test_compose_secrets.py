"""`config set --secret [--web]` on Compose.

The value goes in .env, where Compose interpolates it into any service that
references ${KEY}; the key name is recorded in config/secret-keys so gitops
and `config get` treat it as a secret whatever it is called. --web lists the
key in config/secret-keys-web, rendered to docker-compose.web-env.yml, which
passes it to the web container. On a gitops instance the value gets an
env.template placeholder and a vars.yaml entry. Kubernetes keeps using
config/secrets.env and config/secrets-web.
"""

import os
import subprocess
import sys

import yaml

import canasta_render_web_env
from mock_ansible import run_module_with_params

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CONFIG_TASKS = os.path.join(REPO_ROOT, "roles", "config", "tasks")
ORCH_TASKS = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks")
GITOPS_TASKS = os.path.join(REPO_ROOT, "roles", "gitops", "tasks")
CLASSIFICATION = os.path.join(REPO_ROOT, "vars", "secret_classification.yml")
GITIGNORE = os.path.join(
    REPO_ROOT, "roles", "gitops", "files", "gitignore.default")

VALUE = "Zq7-unlikely-secret-value"


def _read(path):
    with open(path) as f:
        return f.read()


def _load(path):
    return yaml.safe_load(_read(path))


def _write_list(inst, keys, defined=None):
    """List keys for --web; .env defines `defined` (default: all of them)."""
    cfg = os.path.join(inst, "config")
    os.makedirs(cfg, exist_ok=True)
    with open(os.path.join(cfg, "secret-keys-web"), "w") as f:
        f.write("".join(k + "\n" for k in keys))
    with open(os.path.join(inst, ".env"), "w") as f:
        f.write("".join("%s=v\n" % k
                        for k in (keys if defined is None else defined)))


class TestRenderModule:
    def test_writes_web_env_from_key_names(self, tmp_dir):
        _write_list(tmp_dir, ["MAPS_API_KEY", "OAUTH_CLIENT", "MAPS_API_KEY"])
        result, failed, _ = run_module_with_params(
            canasta_render_web_env, {"instance_path": tmp_dir})
        assert not failed and result["changed"] is True
        data = yaml.safe_load(
            _read(os.path.join(tmp_dir, "docker-compose.web-env.yml")))
        assert data == {"services": {"web": {"environment": [
            "MAPS_API_KEY=${MAPS_API_KEY}",
            "OAUTH_CLIENT=${OAUTH_CLIENT}"]}}}

    def test_removes_layer_when_list_empty(self, tmp_dir):
        path = os.path.join(tmp_dir, "docker-compose.web-env.yml")
        with open(path, "w") as f:
            f.write("services: {}\n")
        _write_list(tmp_dir, [])
        result, failed, _ = run_module_with_params(
            canasta_render_web_env, {"instance_path": tmp_dir})
        assert not failed and result["changed"] is True
        assert not os.path.exists(path)

    def test_no_list_no_layer(self, tmp_dir):
        result, failed, _ = run_module_with_params(
            canasta_render_web_env, {"instance_path": tmp_dir})
        assert not failed and result["changed"] is False
        assert not os.path.exists(
            os.path.join(tmp_dir, "docker-compose.web-env.yml"))

    def test_idempotent(self, tmp_dir):
        _write_list(tmp_dir, ["A_KEY"])
        run_module_with_params(canasta_render_web_env,
                               {"instance_path": tmp_dir})
        result, _, _ = run_module_with_params(
            canasta_render_web_env, {"instance_path": tmp_dir})
        assert result["changed"] is False

    def test_leaves_out_keys_this_host_has_no_value_for(self, tmp_dir):
        _write_list(tmp_dir, ["SET_HERE", "SET_ELSEWHERE"],
                    defined=["SET_HERE"])
        result, failed, _ = run_module_with_params(
            canasta_render_web_env, {"instance_path": tmp_dir})
        assert not failed
        assert result["keys"] == ["SET_HERE"]
        assert result["missing"] == ["SET_ELSEWHERE"]
        data = yaml.safe_load(
            _read(os.path.join(tmp_dir, "docker-compose.web-env.yml")))
        assert data == {"services": {"web": {"environment": [
            "SET_HERE=${SET_HERE}"]}}}

    def test_no_layer_when_no_listed_key_has_a_value(self, tmp_dir):
        _write_list(tmp_dir, ["SET_ELSEWHERE"], defined=[])
        result, failed, _ = run_module_with_params(
            canasta_render_web_env, {"instance_path": tmp_dir})
        assert not failed and result["keys"] == []
        assert not os.path.exists(
            os.path.join(tmp_dir, "docker-compose.web-env.yml"))

    def test_rejects_invalid_names(self, tmp_dir):
        _write_list(tmp_dir, ["bad-name"])
        _, failed, msg = run_module_with_params(
            canasta_render_web_env, {"instance_path": tmp_dir})
        assert failed and "bad-name" in msg


class TestComposeLayering:
    def _order(self, body, a, b):
        return body.index("'%s'], [])" % a) < body.index("'%s'], [])" % b)

    def test_start_renders_and_layers_before_override(self):
        body = _read(os.path.join(ORCH_TASKS, "start.yml"))
        assert "canasta_render_web_env" in body
        assert self._order(body, "docker-compose.sidecars.yml",
                           "docker-compose.web-env.yml")
        assert self._order(body, "docker-compose.web-env.yml",
                           "docker-compose.override.yml")

    def test_stop_and_destroy_layer_it(self):
        for name in ("stop.yml", "destroy.yml"):
            body = _read(os.path.join(ORCH_TASKS, name))
            assert self._order(body, "docker-compose.web-env.yml",
                               "docker-compose.override.yml"), name

    def test_update_config_renders_on_compose_only(self):
        tasks = _load(os.path.join(ORCH_TASKS, "update_config.yml"))
        render = [t for t in tasks if "canasta_render_web_env" in t]
        assert render and "compose" in str(render[0]["when"])

    def test_gitops_pull_renders_and_restarts_on_list_change(self):
        body = _read(os.path.join(GITOPS_TASKS, "pull_compose.yml"))
        assert "canasta_render_web_env" in body
        assert "config/secret-keys-web$" in body

    def test_generated_layer_ignored_and_lists_tracked(self):
        rules = _read(GITIGNORE).splitlines()
        assert "docker-compose.web-env.yml" in rules
        for name in ("config/secret-keys", "config/secret-keys-web"):
            assert name not in rules


class TestDispatch:
    def test_set_and_unset_dispatch_by_orchestrator(self):
        body = _read(os.path.join(ORCH_TASKS, "config_set_secret.yml"))
        assert "_set_secret_compose.yml" in body
        assert "_set_secret_kubernetes.yml" in body
        assert "_unset_secret_compose.yml" in _read(
            os.path.join(ORCH_TASKS, "config_unset_secret.yml"))
        assert "config_unset_secret.yml" in _read(
            os.path.join(CONFIG_TASKS, "_unset_secret.yml"))

    def test_kubernetes_still_writes_secrets_env(self):
        body = _read(os.path.join(CONFIG_TASKS, "_set_secret_kubernetes.yml"))
        assert "config/secrets.env" in body
        assert "config/secrets-web" in body

    def test_compose_value_write_is_no_log(self):
        tasks = _load(os.path.join(CONFIG_TASKS, "_set_secret_compose.yml"))
        write = [t for t in tasks if t["name"] == "Write each secret to .env"]
        assert write and write[0]["no_log"] is True

    def test_init_and_join_classify_listed_keys(self):
        for name in ("init_compose.yml", "join.yml"):
            assert "config/secret-keys" in _read(
                os.path.join(GITOPS_TASKS, name)), name


def _instance(tmp_path, gitops):
    inst = tmp_path / "inst"
    (inst / "config").mkdir(parents=True)
    (inst / ".env").write_text("MW_SITE_FQDN=h1.example.com\n")
    (inst / "config" / "secrets.env").write_text("OAUTH_CLIENT=stale\n")
    (inst / "config" / "secrets-web").write_text("OAUTH_CLIENT\n")
    if gitops:
        (inst / "hosts" / "h1").mkdir(parents=True)
        (inst / ".gitops-host").write_text("h1\n")
        (inst / "env.template").write_text(
            "MW_SITE_FQDN={{mw_site_fqdn}}\n")
        (inst / "hosts" / "h1" / "vars.yaml").write_text(
            "mw_site_fqdn: h1.example.com\n")
        subprocess.run(["git", "init", "-q"], cwd=inst, check=True)
    return inst


def _play(tmp_path, inst, tasks_file, extra):
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": dict({"instance_path": str(inst), "canasta_root": REPO_ROOT,
                      "instance_orchestrator": "compose"}, **extra),
        "tasks": [
            {"ansible.builtin.include_vars": {"file": CLASSIFICATION}},
            {"ansible.builtin.include_tasks": tasks_file},
        ],
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_CONFIG=os.path.join(REPO_ROOT, "ansible.cfg"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    return subprocess.run(
        [playbook, "-i", "localhost,", str(play)],
        capture_output=True, text=True, env=env, cwd=REPO_ROOT)


def _set(tmp_path, inst):
    return _play(tmp_path, inst,
                 os.path.join(CONFIG_TASKS, "_set_secret_compose.yml"),
                 {"_config_settings": "OAUTH_CLIENT=%s" % VALUE, "web": True})


def _unset(tmp_path, inst):
    return _play(tmp_path, inst,
                 os.path.join(CONFIG_TASKS, "_unset_secret_compose.yml"),
                 {"keys": "OAUTH_CLIENT MW_SITE_FQDN"})


class TestComposeRoundTrip:
    def test_set_then_unset_plain_instance(self, tmp_path):
        inst = _instance(tmp_path, gitops=False)
        proc = _set(tmp_path, inst)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert VALUE not in proc.stdout + proc.stderr
        assert "OAUTH_CLIENT=%s" % VALUE in (inst / ".env").read_text()
        assert (inst / "config" / "secret-keys").read_text() == "OAUTH_CLIENT\n"
        assert (inst / "config" / "secret-keys-web").read_text() == (
            "OAUTH_CLIENT\n")
        layer = (inst / "docker-compose.web-env.yml").read_text()
        assert "OAUTH_CLIENT=${OAUTH_CLIENT}" in layer
        assert VALUE not in layer
        # The copy Compose never read is dropped.
        assert "OAUTH_CLIENT" not in (inst / "config" / "secrets.env").read_text()
        assert "OAUTH_CLIENT" not in (inst / "config" / "secrets-web").read_text()

        proc = _unset(tmp_path, inst)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        env = (inst / ".env").read_text()
        assert "OAUTH_CLIENT" not in env
        # Not set with --secret, so --secret does not remove it.
        assert "MW_SITE_FQDN=h1.example.com" in env
        assert (inst / "config" / "secret-keys").read_text() == ""
        assert not (inst / "docker-compose.web-env.yml").exists()

    def test_set_then_unset_gitops_instance(self, tmp_path):
        inst = _instance(tmp_path, gitops=True)
        proc = _set(tmp_path, inst)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert VALUE not in proc.stdout + proc.stderr
        # Not matched by the name classifier, still a placeholder.
        template = (inst / "env.template").read_text()
        assert "OAUTH_CLIENT={{oauth_client}}" in template
        assert VALUE not in template
        host_vars = yaml.safe_load(
            (inst / "hosts" / "h1" / "vars.yaml").read_text())
        assert host_vars["oauth_client"] == VALUE
        staged = subprocess.run(
            ["git", "diff", "--cached", "--name-only"], cwd=inst,
            capture_output=True, text=True, check=True).stdout.split()
        for path in ("env.template", "hosts/h1/vars.yaml",
                     "config/secret-keys", "config/secret-keys-web"):
            assert path in staged

        proc = _unset(tmp_path, inst)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "OAUTH_CLIENT" not in (inst / "env.template").read_text()
        assert "MW_SITE_FQDN={{mw_site_fqdn}}" in (
            inst / "env.template").read_text()
        host_vars = yaml.safe_load(
            (inst / "hosts" / "h1" / "vars.yaml").read_text())
        assert "oauth_client" not in host_vars
        assert host_vars["mw_site_fqdn"] == "h1.example.com"
