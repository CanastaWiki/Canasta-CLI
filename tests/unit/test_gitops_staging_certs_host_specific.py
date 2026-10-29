"""CANASTA_STAGING_CERTS is host-specific in gitops, and never shared.

Writing it on a gitops instance gives env.template a placeholder and the
host's own vars.yaml the value, even where the template holds a literal: a
literal would put every host on the same certificates. Join keeps the value
the joining instance was created with, and a pull that refuses over a .env
change says how to discard it without the next pull writing it back.

The real task files run here against temporary gitops checkouts.
"""

import base64
import os
import subprocess
import sys

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CLASSIFIER = os.path.join(REPO_ROOT, "vars", "secret_classification.yml")
TASKS = os.path.join(REPO_ROOT, "roles")
ENSURE = os.path.join(TASKS, "config", "tasks", "_ensure_env_template_placeholder.yml")
SYNC = os.path.join(TASKS, "gitops", "tasks", "sync_env.yml")
JOIN = os.path.join(TASKS, "gitops", "tasks", "join.yml")
PULL = os.path.join(TASKS, "gitops", "tasks", "pull_compose.yml")


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "commit.gpgsign=false", *args],
        check=True, capture_output=True, text=True).stdout


def _status(repo):
    return _git(repo, "status", "--porcelain", "--untracked-files=no")


def _repo(tmp_path, template, hosts):
    inst = tmp_path / "inst"
    inst.mkdir()
    _git(inst, "init", "-q")
    _git(inst, "config", "user.email", "t@example.com")
    _git(inst, "config", "user.name", "t")
    (inst / "env.template").write_text(template)
    for name, host_vars in hosts.items():
        (inst / "hosts" / name).mkdir(parents=True)
        (inst / "hosts" / name / "vars.yaml").write_text(
            yaml.safe_dump(host_vars))
    (inst / "hosts" / "hosts.yaml").write_text(yaml.safe_dump(
        {"hosts": [{"name": n, "role": "sink"} for n in hosts]}))
    _git(inst, "add", "-A")
    _git(inst, "commit", "-q", "-m", "init")
    return inst


def _play(tmp_path, inst, tasks, extra=None):
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars_files": [CLASSIFIER],
        "vars": dict({"instance_path": str(inst), "canasta_root": REPO_ROOT,
                      "ansible_python_interpreter": sys.executable},
                     **(extra or {})),
        "tasks": tasks,
    }]))
    env = {k: v for k, v in os.environ.items() if not k.startswith("ANSIBLE_")}
    env.update(ANSIBLE_CONFIG=os.path.join(REPO_ROOT, "ansible.cfg"),
               ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_HOME=str(tmp_path / "ansible-home"),
               ANSIBLE_COLLECTIONS_PATH=str(tmp_path / "collections"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env, cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def _ensure(tmp_path, inst, setting):
    _play(tmp_path, inst, [{"ansible.builtin.include_tasks": ENSURE}],
          {"_ep_setting": setting})


def _vars(inst, host):
    return yaml.safe_load((inst / "hosts" / host / "vars.yaml").read_text())


def _classifier():
    with open(CLASSIFIER) as f:
        return yaml.safe_load(f)


def test_staging_certs_is_host_specific_and_never_shared():
    c = _classifier()
    assert "CANASTA_STAGING_CERTS" in c["canasta_host_specific_nonsecret"]
    assert c["canasta_host_specific_never_shared"] == {
        "CANASTA_STAGING_CERTS": "false"}


def test_never_shared_keys_are_host_specific():
    c = _classifier()
    assert set(c["canasta_host_specific_never_shared"]) <= set(
        c["canasta_host_specific_nonsecret"])


def test_absent_key_gets_a_placeholder(tmp_path):
    inst = _repo(tmp_path, "HTTP_PORT=80\n", {"prod": {}, "test": {}})
    _ensure(tmp_path, inst, "CANASTA_STAGING_CERTS=true")
    assert "CANASTA_STAGING_CERTS={{canasta_staging_certs}}" in (
        inst / "env.template").read_text().splitlines()


def test_literal_of_the_unset_value_becomes_a_placeholder_alone(tmp_path):
    inst = _repo(tmp_path, "CANASTA_STAGING_CERTS=false\n",
                 {"prod": {"a": 1}, "test": {"a": 2}})
    _ensure(tmp_path, inst, "CANASTA_STAGING_CERTS=true")

    assert (inst / "env.template").read_text().splitlines() == [
        "CANASTA_STAGING_CERTS={{canasta_staging_certs}}"]
    assert _vars(inst, "prod") == {"a": 1}
    assert _vars(inst, "test") == {"a": 2}
    assert _status(inst) == "M  env.template\n"


def test_other_literal_is_copied_to_each_host_without_a_value(tmp_path):
    inst = _repo(tmp_path, 'CANASTA_STAGING_CERTS="true"\n', {
        "prod": {"a": 1},
        "test": {"canasta_staging_certs": "false"},
    })
    (inst / "hosts" / "_shared").mkdir()
    (inst / "hosts" / "_shared" / "vars.yaml").write_text("smtp_user: x\n")
    _ensure(tmp_path, inst, "CANASTA_STAGING_CERTS=false")

    assert (inst / "env.template").read_text().splitlines() == [
        "CANASTA_STAGING_CERTS={{canasta_staging_certs}}"]
    assert _vars(inst, "prod") == {"a": 1, "canasta_staging_certs": "true"}
    assert _vars(inst, "test") == {"canasta_staging_certs": "false"}
    assert (inst / "hosts" / "_shared" / "vars.yaml").read_text() == (
        "smtp_user: x\n")
    assert sorted(_status(inst).splitlines()) == [
        "M  env.template", "M  hosts/prod/vars.yaml"]


def test_other_host_specific_literals_stay_shared(tmp_path):
    inst = _repo(tmp_path, "CANASTA_ENABLE_VARNISH=true\n", {"prod": {}})
    _ensure(tmp_path, inst, "CANASTA_ENABLE_VARNISH=false")
    assert (inst / "env.template").read_text() == "CANASTA_ENABLE_VARNISH=true\n"


def _sync(tmp_path, inst):
    _play(tmp_path, inst, [{"ansible.builtin.include_tasks": SYNC}])


def _sink_with_a_staging_edit(tmp_path):
    inst = _repo(tmp_path, "HTTP_PORT=80\nCANASTA_STAGING_CERTS=false\n",
                 {"staging": {}})
    (inst / ".gitops-host").write_text("staging\n")
    (inst / ".gitops-applied").write_text(_git(inst, "rev-parse", "HEAD"))
    (inst / ".env").write_text("HTTP_PORT=80\nCANASTA_STAGING_CERTS=true\n")
    _sync(tmp_path, inst)
    return inst


def test_sync_saves_the_value_in_the_host_vars_not_the_template(tmp_path):
    inst = _sink_with_a_staging_edit(tmp_path)

    assert "CANASTA_STAGING_CERTS={{canasta_staging_certs}}" in (
        inst / "env.template").read_text().splitlines()
    assert _vars(inst, "staging") == {"canasta_staging_certs": "true"}


def _pull_refusal(inst, sync_paths):
    task = next(t for t in yaml.safe_load(open(PULL))
                if t.get("name") == "Fail if uncommitted changes exist")
    out = _status(inst)
    v = {
        "instance_path": str(inst),
        "_pull_uncommitted": {"stdout": out, "stdout_lines": out.splitlines()},
        "_pull_sync_paths": sync_paths,
        "_pull_commit_sync": False,
        "_sync_env_host_raw": {"content": base64.b64encode(b"staging").decode()},
        "_pull_hosts_raw": {"content": base64.b64encode(
            (inst / "hosts" / "hosts.yaml").read_bytes()).decode()},
        "_sync_env_plan": {"set": {"CANASTA_STAGING_CERTS": "true"}, "unset": []},
    }

    def trust(x):
        if isinstance(x, str):
            return trust_as_template(x)
        return [trust(i) for i in x] if isinstance(x, list) else x

    for name, expr in task["vars"].items():
        v[name] = Templar(loader=DataLoader(), variables=v).template(trust(expr))
    msg = Templar(loader=DataLoader(), variables=v).template(
        trust(task["ansible.builtin.fail"]["msg"]))
    return v["_pull_discard"], msg


def test_the_pull_refusal_names_the_key_and_the_sink_role(tmp_path):
    inst = _sink_with_a_staging_edit(tmp_path)
    _, msg = _pull_refusal(inst, ["env.template", "hosts/staging/vars.yaml"])
    assert "CANASTA_STAGING_CERTS" in msg
    assert "role 'sink' and cannot push" in msg


def test_discarding_as_the_pull_advises_does_not_come_back(tmp_path):
    inst = _sink_with_a_staging_edit(tmp_path)
    discard, msg = _pull_refusal(
        inst, ["env.template", "hosts/staging/vars.yaml"])
    assert discard == [
        "'git restore --source=HEAD --worktree -- "
        "env.template hosts/staging/vars.yaml'"]
    assert discard[0] in msg

    _git(inst, "restore", "--source=HEAD", "--worktree", "--",
         "env.template", "hosts/staging/vars.yaml")
    _sync(tmp_path, inst)
    assert _status(inst) == ""


def test_discarding_the_index_too_writes_the_change_back(tmp_path):
    inst = _sink_with_a_staging_edit(tmp_path)
    _git(inst, "checkout", "HEAD", "--", "env.template",
         "hosts/staging/vars.yaml")
    _sync(tmp_path, inst)
    assert _status(inst) != ""


def test_other_files_are_discarded_from_head(tmp_path):
    inst = _sink_with_a_staging_edit(tmp_path)
    (inst / "hosts" / "hosts.yaml").write_text("hosts: []\n")
    discard, _ = _pull_refusal(
        inst, ["env.template", "hosts/staging/vars.yaml"])
    assert discard[-1] == "'git checkout HEAD -- hosts/hosts.yaml'"


def test_join_placeholders_the_host_specific_keys_before_rendering():
    with open(JOIN) as f:
        tasks = yaml.safe_load(f)
    names = [t.get("name") for t in tasks]
    ensure = tasks[names.index(
        "Ensure env.template has placeholders for this host's own keys")]
    assert ensure["ansible.builtin.include_tasks"].endswith(
        "roles/config/tasks/_ensure_env_template_placeholder.yml")
    assert "canasta_host_specific_nonsecret" in ensure["loop"]
    assert names.index("Write host vars") < names.index(
        "Ensure env.template has placeholders for this host's own keys") < (
        names.index("Render .env and wikis.yaml from templates"))
    assert names.index("Render .env and wikis.yaml from templates") < (
        names.index("Stage the host registration and this host's vars"))


def test_join_keeps_a_staging_host_value_over_a_production_literal(tmp_path):
    inst = _repo(tmp_path, "HTTP_PORT=80\nCANASTA_STAGING_CERTS=false\n",
                 {"prod": {}, "staging": {"canasta_staging_certs": "true"}})
    with open(JOIN) as f:
        ensure = next(t for t in yaml.safe_load(f) if t.get("name") == (
            "Ensure env.template has placeholders for this host's own keys"))
    _play(tmp_path, inst, [
        {"ansible.builtin.set_fact": {"_join_env": {"variables": {
            "HTTP_PORT": "80", "CANASTA_STAGING_CERTS": "true"}}}},
        ensure,
        {"ansible.builtin.copy": {
            "dest": str(inst / ".env"),
            "content": "{{ lookup('file', instance_path ~ '/env.template')"
                       " | canasta_env_render(lookup('file', instance_path"
                       " ~ '/hosts/staging/vars.yaml') | from_yaml) }}"}},
    ])
    assert "CANASTA_STAGING_CERTS=true" in (inst / ".env").read_text().splitlines()
    assert "HTTP_PORT=80" in (inst / "env.template").read_text().splitlines()
