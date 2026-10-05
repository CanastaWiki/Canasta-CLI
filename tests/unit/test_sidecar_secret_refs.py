"""Secret variables referenced by sidecars never land in a cleartext file.

A secret variable is one whose name matches the secret classifier or that was
set with `config set --secret` (config/secret-keys on Compose, the keys of
config/secrets.env on Kubernetes).

Compose interpolates a sidecar's ${VAR} from .env at runtime, so the only
cleartext copy would be the web env bridge: secrets are left out of it, and
each start names them. Kubernetes resolves ${VAR} into the rendered values,
so a plain reference to a secret is refused unless the env key is taken from
the app Secret (envSecret).

On Compose the bridge is rendered per host and gitignored; the upgrade's
.gitignore refresh untracks it in existing repos.
"""

import os
import subprocess
import sys

import pytest
import yaml

import canasta_render_sidecars
import canasta_sidecar_render as render
from _classifier import secret_key_regex
from mock_ansible import run_module_with_params

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ORCH_TASKS = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks")
GITOPS = os.path.join(REPO_ROOT, "roles", "gitops")
CLASSIFICATION = os.path.join(REPO_ROOT, "vars", "secret_classification.yml")
BRIDGE = "config/settings/global/00-canasta-sidecar-env.php"
SECRET_RE = secret_key_regex()

LINKER = {"name": "linker", "image": "example/linker:1.0",
          "env": {"DB_PASS": "${MYSQL_PASSWORD}",
                  "API": "${LINKER_API}",
                  "UPSTREAM": "${LINKER_HOST:-linker}"}}


def _instance(tmp_path, sidecars, env, files=None):
    inst = tmp_path / "inst"
    (inst / "config").mkdir(parents=True)
    (inst / ".env").write_text("".join("%s=%s\n" % kv for kv in env.items()))
    (inst / "config" / "sidecars.yaml").write_text(
        yaml.safe_dump({"sidecars": sidecars}))
    for name, text in (files or {}).items():
        (inst / name).write_text(text)
    return inst


def _render(inst, orchestrator, check_mode=False):
    return run_module_with_params(canasta_render_sidecars, {
        "instance_path": str(inst), "orchestrator": orchestrator,
        "secret_key_regex": SECRET_RE, "_check_mode": check_mode})


def _bridge(inst):
    path = inst / BRIDGE
    return path.read_text() if path.exists() else ""


ENV = {"MYSQL_PASSWORD": "dbpw", "LINKER_API": "apikey",
       "LINKER_HOST": "linker.internal"}


class TestComposeBridge:
    def test_secrets_left_out_non_secrets_kept(self, tmp_path):
        inst = _instance(tmp_path, [LINKER], ENV,
                         {"config/secret-keys": "LINKER_API\n"})
        result, failed, msg = _render(inst, "compose")
        assert not failed, msg
        php = _bridge(inst)
        assert "putenv('LINKER_HOST=linker.internal');" in php
        assert "MYSQL_PASSWORD" not in php and "dbpw" not in php
        assert "LINKER_API" not in php and "apikey" not in php
        assert result["bridge_omitted"] == ["LINKER_API", "MYSQL_PASSWORD"]

    def test_sidecar_still_gets_secret_refs(self, tmp_path):
        inst = _instance(tmp_path, [LINKER], ENV)
        _, failed, msg = _render(inst, "compose")
        assert not failed, msg
        rendered = yaml.safe_load(
            (inst / "docker-compose.sidecars.yml").read_text())
        env = rendered["services"]["linker"]["environment"]
        assert env["DB_PASS"] == "${MYSQL_PASSWORD}"

    def test_secret_in_command_not_refused(self, tmp_path):
        sidecar = {"name": "x", "image": "x:1",
                   "command": ["run", "--token", "${MYSQL_PASSWORD}"]}
        inst = _instance(tmp_path, [sidecar], ENV)
        _, failed, msg = _render(inst, "compose")
        assert not failed, msg

    def test_web_secrets_not_reported(self, tmp_path):
        inst = _instance(tmp_path, [LINKER], ENV,
                         {"config/secret-keys": "LINKER_API\n",
                          "config/secret-keys-web": "LINKER_API\n"})
        result, _, _ = _render(inst, "compose")
        assert result["bridge_omitted"] == ["MYSQL_PASSWORD"]

    def test_absent_secret_not_reported(self, tmp_path):
        inst = _instance(tmp_path, [LINKER], {"LINKER_HOST": "h"})
        result, _, _ = _render(inst, "compose")
        assert result["bridge_omitted"] == []

    def test_only_secrets_removes_bridge(self, tmp_path):
        sidecar = {"name": "x", "image": "x:1",
                   "env": {"P": "${MYSQL_PASSWORD}"}}
        inst = _instance(tmp_path, [sidecar], ENV)
        (inst / BRIDGE).parent.mkdir(parents=True)
        (inst / BRIDGE).write_text("<?php putenv('MYSQL_PASSWORD=dbpw');\n")
        _render(inst, "compose")
        assert not (inst / BRIDGE).exists()


class TestKubernetesRefusal:
    def test_env_ref_refused(self, tmp_path):
        inst = _instance(tmp_path, [LINKER], ENV)
        _, failed, msg = _render(inst, "kubernetes")
        assert failed
        assert "sidecar 'linker'" in msg and "DB_PASS" in msg
        assert "MYSQL_PASSWORD" in msg
        assert "canasta config set --secret DB_PASS=VALUE" in msg
        assert "envSecret" in msg
        assert not (inst / "values-sidecars.yaml").exists()

    def test_refused_in_check_mode(self, tmp_path):
        inst = _instance(tmp_path, [LINKER], ENV)
        _, failed, _ = _render(inst, "kubernetes", check_mode=True)
        assert failed

    def test_envsecret_allowed(self, tmp_path):
        sidecar = dict(LINKER, envSecret=["DB_PASS"],
                       env={"DB_PASS": "${MYSQL_PASSWORD}",
                            "UPSTREAM": "${LINKER_HOST}"})
        inst = _instance(tmp_path, [sidecar], ENV)
        _, failed, msg = _render(inst, "kubernetes")
        assert not failed, msg
        values = (inst / "values-sidecars.yaml").read_text()
        assert "dbpw" not in values
        assert "dbpw" not in _bridge(inst)

    def test_envprivate_does_not_exempt(self, tmp_path):
        sidecar = {"name": "x", "image": "x:1", "envPrivate": ["P"],
                   "env": {"P": "${MYSQL_PASSWORD}"}}
        inst = _instance(tmp_path, [sidecar], ENV)
        _, failed, _ = _render(inst, "kubernetes")
        assert failed

    def test_recorded_secret_refused(self, tmp_path):
        sidecar = {"name": "x", "image": "x:1",
                   "env": {"OAUTH": "${OAUTH_CLIENT}"}}
        inst = _instance(tmp_path, [sidecar], {},
                         {"config/secrets.env": "OAUTH_CLIENT=abc\n"})
        _, failed, msg = _render(inst, "kubernetes")
        assert failed and "OAUTH_CLIENT" in msg

    @pytest.mark.parametrize("command", [
        ["run", "--pw=${MYSQL_PASSWORD}"], "run --pw=${MYSQL_PASSWORD}"])
    def test_command_ref_refused(self, tmp_path, command):
        sidecar = {"name": "x", "image": "x:1", "command": command}
        inst = _instance(tmp_path, [sidecar], ENV)
        _, failed, msg = _render(inst, "kubernetes")
        assert failed
        assert "command references the secret variable MYSQL_PASSWORD" in msg

    def test_non_secret_refs_allowed(self, tmp_path):
        sidecar = {"name": "x", "image": "x:1",
                   "command": "serve ${LINKER_HOST}",
                   "env": {"UPSTREAM": "${LINKER_HOST}"}}
        inst = _instance(tmp_path, [sidecar], ENV)
        _, failed, msg = _render(inst, "kubernetes")
        assert not failed, msg


def test_validate_is_pure():
    def is_secret(name):
        return name == "S"
    assert render.validate_k8s_secret_refs(
        [{"name": "a", "env": {"K": "${S}"}, "envSecret": ["K"]}],
        is_secret) is None
    assert render.validate_k8s_secret_refs(
        [{"name": "a", "env": {"K": "x-${S:-d}"}}], is_secret) is not None


# --- Wiring ---------------------------------------------------------------- #

def _yaml(path):
    with open(path) as handle:
        return yaml.safe_load(handle)


def _walk(tasks):
    for task in tasks or []:
        yield task
        for key in ("block", "rescue", "always"):
            yield from _walk(task.get(key))


def _render_call_sites():
    sites = []
    for root, _, files in os.walk(os.path.join(REPO_ROOT, "roles")):
        for name in files:
            path = os.path.join(root, name)
            if not name.endswith(".yml") or "/tasks" not in path:
                continue
            for task in _walk(_yaml(path)):
                if "canasta_render_sidecars" in task:
                    sites.append((path, task))
    return sites


def test_every_render_passes_the_classifier():
    sites = _render_call_sites()
    assert len(sites) >= 5
    for path, task in sites:
        assert task["canasta_render_sidecars"].get("secret_key_regex") == (
            "{{ canasta_secret_key_regex }}"), path


def test_start_reports_omitted_secrets_after_each_render():
    tasks = list(_walk(_yaml(os.path.join(ORCH_TASKS, "start.yml"))))
    for i, task in enumerate(tasks):
        if "canasta_render_sidecars" in task:
            assert task.get("register") == "_sidecar_render"
            assert tasks[i + 1].get("ansible.builtin.include_tasks") == (
                "report_bridge_omitted.yml")


def test_restart_reports_through_start():
    tasks = _yaml(os.path.join(REPO_ROOT, "roles", "instance_lifecycle",
                               "tasks", "restart.yml"))
    starts = [t for t in tasks if (t.get("ansible.builtin.include_role")
                                   or {}).get("tasks_from") == "start.yml"]
    assert starts


# --- Playbook runs --------------------------------------------------------- #

def _play(tmp_path, inst, tasks, orchestrator="compose"):
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {"instance_path": str(inst), "canasta_root": REPO_ROOT,
                 "instance_orchestrator": orchestrator,
                 "ansible_python_interpreter": sys.executable},
        "tasks": [{"ansible.builtin.include_vars": {"file": CLASSIFICATION}}]
        + tasks,
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_CONFIG=os.path.join(REPO_ROOT, "ansible.cfg"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    return subprocess.run(
        [playbook, "-i", "localhost,", str(play)],
        capture_output=True, text=True, env=env, cwd=REPO_ROOT)


def test_report_prints_omitted_secrets(tmp_path):
    inst = _instance(tmp_path, [LINKER], ENV,
                     {"config/secret-keys": "LINKER_API\n"})
    proc = _play(tmp_path, inst, [
        {"canasta_render_sidecars": {
            "instance_path": "{{ instance_path }}", "orchestrator": "compose",
            "secret_key_regex": "{{ canasta_secret_key_regex }}"},
         "register": "_sidecar_render"},
        {"ansible.builtin.include_tasks":
            os.path.join(ORCH_TASKS, "report_bridge_omitted.yml")},
    ])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert ("because they are secrets: LINKER_API, MYSQL_PASSWORD. Sidecars "
            "still receive them. If MediaWiki needs them, use 'canasta config "
            "set --secret --web KEY=VALUE'.") in proc.stdout
    assert "canasta config set --secret --web KEY=VALUE" in proc.stdout


# --- Gitignore ------------------------------------------------------------- #

def _read(path):
    with open(path) as handle:
        return handle.read()


def test_bridge_ignored_on_compose_only():
    assert BRIDGE in _read(os.path.join(GITOPS, "files", "gitignore.compose"))
    assert BRIDGE not in _read(os.path.join(GITOPS, "files",
                                            "gitignore.default"))
    assert "gitignore.compose" in _read(
        os.path.join(GITOPS, "tasks", "init_compose.yml"))
    assert "gitignore.compose" not in _read(
        os.path.join(GITOPS, "tasks", "init_kubernetes.yml"))


def _git(inst, *args):
    return subprocess.run(["git", *args], cwd=inst, capture_output=True,
                          text=True, check=True).stdout


def _gitops_repo(tmp_path):
    """A gitops checkout from before the rule: the bridge is committed."""
    inst = tmp_path / "inst"
    (inst / "config" / "settings" / "global").mkdir(parents=True)
    (inst / ".gitops-host").write_text("h1\n")
    (inst / ".gitignore").write_text(".gitops-host\n")
    (inst / BRIDGE).write_text("<?php putenv('A=1');\n")
    _git(inst, "init", "-q")
    _git(inst, "add", ".gitignore", BRIDGE)
    _git(inst, "-c", "user.name=t", "-c", "user.email=t@example.com",
         "-c", "commit.gpgsign=false", "commit", "-q", "-m", "init")
    return inst


def _refresh(tmp_path, inst, orchestrator):
    return _play(tmp_path, inst, [{"ansible.builtin.include_tasks": os.path.join(
        GITOPS, "tasks", "refresh_gitignore.yml")}], orchestrator)


def test_refresh_untracks_bridge_on_compose(tmp_path):
    inst = _gitops_repo(tmp_path)
    proc = _refresh(tmp_path, inst, "compose")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "canasta gitops push" in proc.stdout
    assert (inst / BRIDGE).exists()
    assert BRIDGE in (inst / ".gitignore").read_text()
    staged = _git(inst, "diff", "--cached", "--name-status")
    assert "D\t" + BRIDGE in staged and "M\t.gitignore" in staged
    assert _git(inst, "status", "--porcelain", "--untracked-files=no") == (
        "M  .gitignore\nD  " + BRIDGE + "\n")
    _git(inst, "-c", "user.name=t", "-c", "user.email=t@example.com",
         "-c", "commit.gpgsign=false", "commit", "-q", "-m", "push")
    # What gitops pull and status see once it is pushed.
    (inst / BRIDGE).write_text("<?php putenv('A=2');\n")
    assert _git(inst, "status", "--porcelain") == ""
    assert _git(inst, "ls-files", BRIDGE) == ""
    # Idempotent.
    proc = _refresh(tmp_path, inst, "compose")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _git(inst, "status", "--porcelain") == ""


def test_refresh_leaves_kubernetes_tracking(tmp_path):
    inst = _gitops_repo(tmp_path)
    proc = _refresh(tmp_path, inst, "kubernetes")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert BRIDGE not in (inst / ".gitignore").read_text()
    assert _git(inst, "ls-files", BRIDGE).strip() == BRIDGE
    assert _git(inst, "diff", "--cached", "--name-only") == ""


def test_pull_flags_restart_when_bridge_removed():
    body = _read(os.path.join(GITOPS, "tasks", "pull_compose.yml"))
    assert "00-canasta-sidecar-env.php" in body
    assert "_pull_bridge.stat.exists" in body
