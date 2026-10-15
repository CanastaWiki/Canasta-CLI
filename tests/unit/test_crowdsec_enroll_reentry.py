"""bouncer_enroll finishes its own block after the restart re-enters it.

Storing the key restarts the instance, and the restart's auto-enroll runs
bouncer_enroll again, which finds the bouncer enrolled and recomputes the
enrollment facts. The outer run must still run the rest of its block.

This runs the real bouncer_enroll.yml with a fake cscli, a stub preflight,
and a stub `config set` that stores the key and re-enters bouncer_enroll
the way the restart's auto-enroll does.
"""

import os
import shutil
import subprocess
import sys

import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ROLES = os.path.join(REPO_ROOT, "roles")

FAKE_CSCLI = """#!/bin/sh
state="$(dirname "$0")/bouncers"
shift
case "$*" in
  "lapi status") exit 0 ;;
  "bouncers list -o json")
    if [ -f "$state" ]; then echo '[{"name": "canasta-caddy"}]'; else echo null; fi ;;
  "bouncers add canasta-caddy -o raw") touch "$state"; echo fresh-key ;;
  "bouncers delete canasta-caddy --ignore-missing") rm -f "$state" ;;
  *) echo "unexpected: $*" >&2; exit 1 ;;
esac
"""

STUB_CONFIG_SET = """\
- name: Store the key
  ansible.builtin.lineinfile:
    path: "{{ instance_path }}/.env"
    line: "{{ config_settings_override }}"
- name: Re-enter bouncer_enroll as the restart's auto-enroll does
  ansible.builtin.include_role:
    name: crowdsec
    tasks_from: bouncer_enroll.yml
  vars:
    bouncer_enroll_auto: true
"""


def _commit_render(inst, caddyfile):
    host = inst / "hosts" / "prod"
    host.mkdir(parents=True)
    (host / "rendered-values.yaml").write_text(yaml.safe_dump(
        {"configData": {"caddy": {"Caddyfile": caddyfile}}}))
    git = ["git", "-C", str(inst), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run(git + ["init", "-q"], check=True)
    subprocess.run(git + ["add", "-A"], check=True)
    subprocess.run(git + ["commit", "-q", "-m", "render"], check=True)


def _run(tmp_path, env_text, k8s=False, gitops=False, committed_caddy=None,
         sops=False):
    roles = tmp_path / "roles"
    cs = roles / "crowdsec" / "tasks"
    cs.mkdir(parents=True)
    shutil.copy(os.path.join(ROLES, "crowdsec", "tasks", "bouncer_enroll.yml"),
                cs / "bouncer_enroll.yml")
    cscli = tmp_path / "cscli-wrapper"
    cscli.write_text(FAKE_CSCLI)
    cscli.chmod(0o755)
    (cs / "_preflight.yml").write_text(
        "- ansible.builtin.set_fact:\n"
        "    _cscli_prefix: %s\n" % cscli)
    cfg = roles / "config" / "tasks"
    cfg.mkdir(parents=True)
    (cfg / "set.yml").write_text(STUB_CONFIG_SET)

    inst = tmp_path / "inst"
    inst.mkdir()
    (inst / ".env").write_text(env_text)
    if gitops:
        (inst / ".gitops-host").write_text("prod\n")
        if sops:
            (inst / ".sops.yaml").write_text("creation_rules: []\n")
        if committed_caddy is not None:
            _commit_render(inst, committed_caddy)
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {"instance_path": str(inst), "instance_id": "demo",
                 "instance_is_k8s": k8s,
                 "ansible_python_interpreter": sys.executable},
        "tasks": [{"ansible.builtin.include_role": {
            "name": "crowdsec", "tasks_from": "bouncer_enroll.yml"},
            "vars": {"bouncer_enroll_auto": True}}],
    }]))
    env = dict(
        os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
        ANSIBLE_ROLES_PATH=str(roles),
        ANSIBLE_LIBRARY=os.path.join(ROLES, "common", "library"),
        ANSIBLE_MODULE_UTILS=os.path.join(ROLES, "common", "module_utils"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env,
                          cwd=tmp_path)
    return proc, inst


def test_outer_enroll_reports_after_the_reentrant_run(tmp_path):
    proc, inst = _run(tmp_path, "CANASTA_ENABLE_CROWDSEC=true\n")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "CROWDSEC_BOUNCER_API_KEY=fresh-key" in (inst / ".env").read_text()
    assert out.count("is enrolled and active for") == 1, out


def test_enrolled_instance_is_a_no_op(tmp_path):
    (tmp_path / "bouncers").touch()
    proc, inst = _run(tmp_path, "CROWDSEC_BOUNCER_API_KEY=old\n")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "is enrolled and active for" not in out
    assert (inst / ".env").read_text() == "CROWDSEC_BOUNCER_API_KEY=old\n"


PUSH_HINT = ("CrowdSec bouncer 'canasta-caddy' is registered for instance "
             "'demo' and its key is stored, but Caddy does not use it until "
             "it is in the gitops repo. Run 'canasta gitops push -i demo' to "
             "activate it")


CADDY_WITHOUT_BOUNCER = "{\n    email admin@example.com\n}\n"
CADDY_WITH_BOUNCER = (
    "{\n    order crowdsec first\n    crowdsec {\n"
    "        api_url http://crowdsec:8080\n"
    "        api_key {env.CROWDSEC_BOUNCER_API_KEY}\n"
    "        ticker_interval 15s\n    }\n}\n")


def test_gitops_kubernetes_enroll_says_to_push_not_active(tmp_path):
    # The committed render predates CrowdSec, so Argo CD deploys a Caddyfile
    # with no bouncer until the next push.
    proc, inst = _run(tmp_path, "CANASTA_ENABLE_CROWDSEC=true\n",
                      k8s=True, gitops=True,
                      committed_caddy=CADDY_WITHOUT_BOUNCER)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "CROWDSEC_BOUNCER_API_KEY=fresh-key" in (inst / ".env").read_text()
    assert out.count(PUSH_HINT) == 1, out
    assert "is enrolled and active for" not in out
    assert "restarting to activate it" not in out


def test_kubernetes_without_gitops_reports_active(tmp_path):
    proc, _ = _run(tmp_path, "CANASTA_ENABLE_CROWDSEC=true\n", k8s=True)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert out.count("is enrolled and active for") == 1, out
    assert "gitops push" not in out


def test_compose_gitops_reports_active(tmp_path):
    proc, _ = _run(tmp_path, "CANASTA_ENABLE_CROWDSEC=true\n", gitops=True)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert out.count("is enrolled and active for") == 1, out
    assert "gitops push" not in out


def test_gitops_kubernetes_with_committed_bouncer_reports_active(tmp_path):
    # A re-enroll: the committed Caddyfile already has the bouncer and the
    # key lives in a Secret the CLI applies, so Caddy uses the new key now.
    proc, _ = _run(tmp_path, "CANASTA_ENABLE_CROWDSEC=true\n",
                   k8s=True, gitops=True, committed_caddy=CADDY_WITH_BOUNCER)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert out.count("is enrolled and active for") == 1, out
    assert "gitops push" not in out


def test_gitops_kubernetes_with_sops_needs_a_push(tmp_path):
    # Argo CD owns the key's Secret, so the new key reaches Caddy only once
    # it is pushed, even with the bouncer already in the committed Caddyfile.
    proc, _ = _run(tmp_path, "CANASTA_ENABLE_CROWDSEC=true\n",
                   k8s=True, gitops=True, committed_caddy=CADDY_WITH_BOUNCER,
                   sops=True)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert out.count(PUSH_HINT) == 1, out
    assert "is enrolled and active for" not in out


def test_gitops_kubernetes_without_a_committed_render_needs_a_push(tmp_path):
    proc, _ = _run(tmp_path, "CANASTA_ENABLE_CROWDSEC=true\n",
                   k8s=True, gitops=True)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert out.count(PUSH_HINT) == 1, out
