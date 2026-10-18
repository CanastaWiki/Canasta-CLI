"""`config set` on a gitops Compose instance adds an env.template placeholder
for a host-specific key, so each host can hold its own value. A key that is
already a literal in env.template is what every host renders; replacing it
with a placeholder would keep the value for the host that ran config set only,
and the others would fall back to the key's default on their next render.
The real _ensure_env_template_placeholder.yml tasks run here against a temp
env.template.
"""

import os
import subprocess
import sys

import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
COMMON = os.path.join(REPO_ROOT, "roles", "common")
ENSURE = os.path.join(
    REPO_ROOT, "roles", "config", "tasks", "_ensure_env_template_placeholder.yml")
CLASSIFIER = os.path.join(REPO_ROOT, "vars", "secret_classification.yml")


def _ensure(tmp_path, template, setting, secret_names=()):
    inst = tmp_path / "inst"
    inst.mkdir()
    (inst / "env.template").write_text(template)
    subprocess.run(["git", "init", "-q"], cwd=inst, check=True)
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars_files": [CLASSIFIER],
        "vars": {"instance_path": str(inst), "_ep_setting": setting,
                 "_config_secret_names": list(secret_names),
                 "ansible_python_interpreter": sys.executable},
        "tasks": [{"ansible.builtin.include_tasks": ENSURE}],
    }]))
    empty = tmp_path / "collections"
    empty.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith("ANSIBLE_")}
    env.update(ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_HOME=str(tmp_path / "ansible-home"),
               ANSIBLE_COLLECTIONS_PATH=str(empty),
               ANSIBLE_LIBRARY=os.path.join(COMMON, "library"),
               ANSIBLE_MODULE_UTILS=os.path.join(COMMON, "module_utils"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env, cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return (inst / "env.template").read_text().splitlines()


def test_tls_mode_is_host_specific():
    with open(CLASSIFIER) as f:
        keys = yaml.safe_load(f)["canasta_host_specific_nonsecret"]
    assert "TLS_MODE" in keys


def test_absent_host_specific_key_gets_a_placeholder(tmp_path):
    lines = _ensure(tmp_path, "MW_SITE_FQDN={{mw_site_fqdn}}\n",
                    "TLS_MODE=internal")
    assert "TLS_MODE={{tls_mode}}" in lines


def test_literal_host_specific_key_stays_literal(tmp_path):
    lines = _ensure(tmp_path, "TLS_MODE=internal\n",
                    "TLS_MODE=acme")
    assert lines == ["TLS_MODE=internal"]


def test_existing_placeholder_is_unchanged(tmp_path):
    lines = _ensure(tmp_path, "TLS_MODE={{tls_mode}}\n",
                    "TLS_MODE=acme")
    assert lines == ["TLS_MODE={{tls_mode}}"]


def test_literal_secret_is_still_moved(tmp_path):
    lines = _ensure(tmp_path, "SMTP_PASSWORD=hunter2\n",
                    "SMTP_PASSWORD=changed")
    assert lines == ["SMTP_PASSWORD={{smtp_password}}"]


def test_literal_secret_by_flag_is_still_moved(tmp_path):
    lines = _ensure(tmp_path, "WEBHOOK_URL=https://example.com/x\n",
                    "WEBHOOK_URL=https://example.com/y",
                    secret_names=["WEBHOOK_URL"])
    assert lines == ["WEBHOOK_URL={{webhook_url}}"]


def test_ordinary_literal_is_left_alone(tmp_path):
    lines = _ensure(tmp_path, "MW_LANG=en\n", "MW_LANG=fr")
    assert lines == ["MW_LANG=en"]
