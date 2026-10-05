"""config set on a gitops Compose instance persists the OpenSearch
credentials that enabling observability generates straight into .env.

They are not among the typed settings, so without this the next pull or
config regenerate re-renders .env from env.template and drops them, and the
Caddyfile render then refuses to run with an empty OS_PASSWORD_HASH.
"""

import os
import subprocess
import sys

import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
UPDATE_VARS = os.path.join(
    REPO_ROOT, "roles", "config", "tasks", "_update_gitops_vars.yml")
UPDATE_VAR = os.path.join(
    REPO_ROOT, "roles", "config", "tasks", "_update_gitops_var.yml")
RENDER_ENV = os.path.join(
    REPO_ROOT, "roles", "gitops", "tasks", "_render_env.yml")
CLASSIFICATION = os.path.join(REPO_ROOT, "vars", "secret_classification.yml")

HASH = "$$2b$$12$$abcdefghijklmnopqrstuuJ0123456789ABCDEFGHIJKLMNOPQRS"


def _load(path):
    with open(path) as f:
        return yaml.safe_load(f)


def _task(tasks, name):
    for t in tasks:
        if t.get("name") == name:
            return t
    raise AssertionError(f"task {name!r} not found")


class TestStructure:
    def test_generated_settings_join_both_loops(self):
        tasks = _load(UPDATE_VARS)
        for name in ("Ensure env.template has placeholders for new credential keys",
                     "Update vars.yaml for each changed key"):
            loop = _task(tasks, name)["loop"]
            assert "_config_generated_settings" in loop, name

    def test_generated_settings_collected_before_placeholders(self):
        names = [t.get("name") for t in _load(UPDATE_VARS)]
        assert (names.index("Collect generated credentials to propagate")
                < names.index(
                    "Ensure env.template has placeholders for new credential keys"))

    def test_value_handling_is_no_log(self):
        tasks = _load(UPDATE_VARS)
        assert _task(tasks, "Read final .env for generated credentials")[
            "no_log"] is True
        assert _task(tasks, "Collect generated credentials to propagate")[
            "no_log"] is True
        tasks = _load(UPDATE_VAR)
        parse = _task(tasks, "Parse key and value")
        assert "_ugv_secret" in str(parse.get("no_log", ""))
        classify = str(_task(tasks, "Classify the key"))
        assert "canasta_secret_key_regex" in classify
        assert "_config_secret_names" in classify


def _run(tmp_path, settings, env_lines, template_lines):
    inst = tmp_path / "inst"
    (inst / "hosts" / "h1").mkdir(parents=True)
    (inst / ".gitops-host").write_text("h1\n")
    (inst / ".env").write_text("\n".join(env_lines) + "\n")
    (inst / "env.template").write_text("\n".join(template_lines) + "\n")
    (inst / "hosts" / "h1" / "vars.yaml").write_text(
        "mw_site_fqdn: h1.example.com\n")
    subprocess.run(["git", "init", "-q"], cwd=inst, check=True)

    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {"instance_path": str(inst), "canasta_root": REPO_ROOT,
                 "_config_settings": settings},
        "tasks": [
            {"ansible.builtin.include_vars": {"file": CLASSIFICATION}},
            {"ansible.builtin.include_tasks": UPDATE_VARS},
            {"ansible.builtin.include_tasks": RENDER_ENV,
             "vars": {"env_render_vars": "{{ lookup('file', instance_path"
                      " + '/hosts/h1/vars.yaml') | from_yaml }}"}},
        ],
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_CONFIG=os.path.join(REPO_ROOT, "ansible.cfg"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run(
        [playbook, "-i", "localhost,", str(play)],
        capture_output=True, text=True, env=env, cwd=REPO_ROOT)
    return proc, inst


class TestRoundTrip:
    def test_generated_credentials_survive_a_render(self, tmp_path):
        proc, inst = _run(
            tmp_path, "CANASTA_ENABLE_OBSERVABILITY=true",
            ["MW_SITE_FQDN=h1.example.com",
             "CANASTA_ENABLE_OBSERVABILITY=true",
             "OS_USER=admin", "OS_PASSWORD=s3cr#t-pw",
             "OS_PASSWORD_HASH=" + HASH],
            ["MW_SITE_FQDN={{mw_site_fqdn}}"])
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "s3cr#t-pw" not in proc.stdout
        assert HASH not in proc.stdout

        template = (inst / "env.template").read_text()
        for key in ("OS_USER", "OS_PASSWORD", "OS_PASSWORD_HASH"):
            assert f"{key}={{{{{key.lower()}}}}}" in template

        stored = yaml.safe_load((inst / "hosts" / "h1" / "vars.yaml").read_text())
        assert stored["os_user"] == "admin"
        assert stored["os_password"] == "s3cr#t-pw"
        assert stored["os_password_hash"] == HASH

        rendered = (inst / ".env").read_text().splitlines()
        assert "OS_USER=admin" in rendered
        assert "OS_PASSWORD=s3cr#t-pw" in rendered
        assert "OS_PASSWORD_HASH=" + HASH in rendered

        staged = subprocess.run(
            ["git", "diff", "--cached", "--name-only"], cwd=inst,
            capture_output=True, text=True, check=True).stdout.split()
        assert "env.template" in staged
        assert "hosts/h1/vars.yaml" in staged

    def test_nothing_added_without_credentials_in_env(self, tmp_path):
        proc, inst = _run(
            tmp_path, "CANASTA_ENABLE_VARNISH=false",
            ["MW_SITE_FQDN=h1.example.com", "CANASTA_ENABLE_VARNISH=false"],
            ["MW_SITE_FQDN={{mw_site_fqdn}}", "CANASTA_ENABLE_VARNISH=false"])
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "OS_" not in (inst / "env.template").read_text()
        stored = yaml.safe_load((inst / "hosts" / "h1" / "vars.yaml").read_text())
        assert not any(k.startswith("os_") for k in stored)
