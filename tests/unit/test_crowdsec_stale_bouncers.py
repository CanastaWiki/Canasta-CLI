"""bouncer_enroll clears stale `canasta-caddy@<ip>` registrations.

CrowdSec auto-creates a `canasta-caddy@<ip>` child each time the caddy
container reaches the LAPI from a new IP. This runs the real
bouncer_enroll.yml against a fake cscli that keeps its bouncer table in a
JSON file and mimics cscli 1.6: deleting the parent removes its children,
and `add --key` registers a bouncer with a given key.
"""

import json
import os
import subprocess
import sys

import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ROLES = os.path.join(REPO_ROOT, "roles")

FAKE_CSCLI = """#!%s
import json, os, sys
here = os.path.dirname(os.path.abspath(sys.argv[0]))
state = os.path.join(here, "bouncers.json")
args = sys.argv[2:]
with open(os.path.join(here, "calls.log"), "a") as log:
    log.write(" ".join(args) + "\\n")
bouncers = json.load(open(state)) if os.path.exists(state) else []
if args == ["lapi", "status"]:
    sys.exit(0)
if args == ["bouncers", "list", "-o", "json"]:
    print(json.dumps(bouncers) if bouncers else "null")
    sys.exit(0)
if args[:2] == ["bouncers", "delete"] and args[2] == "canasta-caddy":
    bouncers = [b for b in bouncers if b["name"] != "canasta-caddy"
                and not b["name"].startswith("canasta-caddy@")]
elif args[:3] == ["bouncers", "add", "canasta-caddy"]:
    key = args[4] if args[3:4] == ["--key"] else "fresh-key"
    bouncers.append({"name": "canasta-caddy", "ip_address": "",
                     "auto_created": False, "key": key})
    print(key)
else:
    sys.stderr.write("unexpected: %%s\\n" %% args)
    sys.exit(1)
json.dump(bouncers, open(state, "w"))
""" % sys.executable

STUB_CONFIG_SET = """\
- name: Record an unexpected key store
  ansible.builtin.lineinfile:
    path: "{{ instance_path }}/.env"
    line: "{{ config_settings_override }}"
"""


def _bouncer(name, ip, auto_created):
    return {"name": name, "ip_address": ip, "auto_created": auto_created,
            "key": "stored-key", "last_pull": "2026-10-01T00:00:00Z"}


def _run(tmp_path, bouncers, auto=True):
    roles = tmp_path / "roles"
    cs = roles / "crowdsec" / "tasks"
    cs.mkdir(parents=True)
    with open(os.path.join(ROLES, "crowdsec", "tasks", "bouncer_enroll.yml")) as f:
        (cs / "bouncer_enroll.yml").write_text(f.read())
    cscli = tmp_path / "cscli-wrapper"
    cscli.write_text(FAKE_CSCLI)
    cscli.chmod(0o755)
    (tmp_path / "bouncers.json").write_text(json.dumps(bouncers))
    (cs / "_preflight.yml").write_text(
        "- ansible.builtin.set_fact:\n"
        "    _cscli_prefix: %s\n"
        "    _cscli_argv: ['%s']\n" % (cscli, cscli))
    cfg = roles / "config" / "tasks"
    cfg.mkdir(parents=True)
    (cfg / "set.yml").write_text(STUB_CONFIG_SET)

    inst = tmp_path / "inst"
    inst.mkdir()
    (inst / ".env").write_text("CROWDSEC_BOUNCER_API_KEY=stored-key\n")
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {"instance_path": str(inst), "instance_id": "demo",
                 "ansible_python_interpreter": sys.executable},
        "tasks": [{"ansible.builtin.include_role": {
            "name": "crowdsec", "tasks_from": "bouncer_enroll.yml"},
            "vars": {"bouncer_enroll_auto": auto}}],
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
    final = json.loads((tmp_path / "bouncers.json").read_text())
    return proc, inst, final


STALE_FAMILY = [
    _bouncer("canasta-caddy", "172.18.0.4", False),
    _bouncer("canasta-caddy@172.18.0.5", "172.18.0.5", True),
    _bouncer("canasta-caddy@172.18.0.3", "172.18.0.3", True),
    _bouncer("firewall", "10.0.0.2", False),
]


def test_stale_children_reset_to_one_registration_with_the_same_key(tmp_path):
    proc, inst, final = _run(tmp_path, STALE_FAMILY)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    family = [b for b in final if b["name"].startswith("canasta-caddy")]
    assert family == [{"name": "canasta-caddy", "ip_address": "",
                       "auto_created": False, "key": "stored-key"}], final
    assert "Removed 2 stale CrowdSec bouncer registration(s)" in out
    # Other bouncers are untouched, and the stored key is kept: no new key
    # is stored, so the instance does not restart.
    assert any(b["name"] == "firewall" for b in final)
    assert (inst / ".env").read_text() == "CROWDSEC_BOUNCER_API_KEY=stored-key\n"
    assert "stored-key" not in out


def test_explicit_enroll_resets_without_the_nothing_to_do_hint(tmp_path):
    proc, _, final = _run(tmp_path, STALE_FAMILY, auto=False)
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    assert "Nothing to do" not in out
    assert [b["name"] for b in final].count("canasta-caddy") == 1
    assert not any(b["name"].startswith("canasta-caddy@") for b in final)


def test_single_registration_is_left_alone(tmp_path):
    proc, inst, final = _run(
        tmp_path, [_bouncer("canasta-caddy", "172.18.0.4", False)])
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    calls = (tmp_path / "calls.log").read_text()
    assert "bouncers delete" not in calls
    assert "bouncers add" not in calls
    assert final[0]["ip_address"] == "172.18.0.4"
