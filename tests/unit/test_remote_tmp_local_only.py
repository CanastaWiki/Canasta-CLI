"""canasta-docker's /tmp remote temp dir must reach local connections only.

On an SSH target /tmp is shared with other accounts, so a predictable base
directory there can be pre-created by another user. canasta-docker exports
CANASTA_LOCAL_REMOTE_TEMP, and canasta.yml's ansible_remote_tmp applies it
only while the connection is local. The play host is rebound to SSH targets
with set_fact, so the choice has to follow the connection per task rather
than be fixed per inventory host.
"""
import os
import subprocess
import sys

import pytest
import yaml


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
CANASTA_YML = os.path.join(REPO_ROOT, "canasta.yml")
ANSIBLE_PLAYBOOK = os.path.join(os.path.dirname(sys.executable),
                                "ansible-playbook")


def _remote_tmp_expr():
    with open(CANASTA_YML) as fh:
        play = yaml.safe_load(fh)[0]
    return play["vars"]["ansible_remote_tmp"]


def _run(tmp_path, env_extra):
    out = tmp_path / "out"
    out.mkdir()
    inventory = tmp_path / "inv.yml"
    inventory.write_text(yaml.safe_dump({"all": {"hosts": {"localhost": {
        "ansible_connection": "local",
        "ansible_python_interpreter": "{{ ansible_playbook_python }}",
    }}}}))
    playbook = tmp_path / "pb.yml"
    playbook.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "gather_facts": False,
        "vars": {"ansible_remote_tmp": _remote_tmp_expr()},
        "tasks": [
            {"ansible.builtin.add_host": {
                "name": "ctl",
                "ansible_connection": "local",
                "ansible_python_interpreter":
                    "{{ ansible_playbook_python }}"}},
            {"ansible.builtin.set_fact": {
                "seen_local": "{{ ansible_remote_tmp }}"}},
            {"ansible.builtin.copy": {
                "content": "x", "dest": str(out / "local")}},
            {"ansible.builtin.set_fact": {
                "ansible_host": "203.0.113.9",
                "ansible_connection": "ssh"}},
            {"ansible.builtin.set_fact": {
                "seen_ssh": "{{ ansible_remote_tmp }}"}},
            {"ansible.builtin.copy": {
                "content": "{{ seen_local }}\n{{ seen_ssh }}\n",
                "dest": str(out / "seen")},
             "delegate_to": "ctl"},
        ],
    }]))
    env = {k: v for k, v in os.environ.items()
           if k not in ("CANASTA_LOCAL_REMOTE_TEMP", "ANSIBLE_REMOTE_TEMP")}
    env.update(env_extra)
    result = subprocess.run(
        [ANSIBLE_PLAYBOOK, "-i", str(inventory), str(playbook)],
        cwd=str(tmp_path), env=env, stdin=subprocess.DEVNULL,
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return (out / "seen").read_text().splitlines()


@pytest.mark.skipif(not os.path.exists(ANSIBLE_PLAYBOOK),
                    reason="ansible-playbook not installed alongside python")
class TestRemoteTmpLocalOnly:

    def test_local_uses_container_temp_and_ssh_keeps_default(self, tmp_path):
        local_tmp = tmp_path / "rtmp"
        seen_local, seen_ssh = _run(
            tmp_path, {"CANASTA_LOCAL_REMOTE_TEMP": str(local_tmp),
                       "ANSIBLE_KEEP_REMOTE_FILES": "1"})
        assert seen_local == str(local_tmp)
        assert seen_ssh == "~/.ansible/tmp"
        # Both the play host's local task and the task delegated to the
        # local controller after the SSH rebind used the override.
        assert len(list(local_tmp.glob("ansible-tmp-*"))) == 2

    def test_without_override_ansible_default_applies(self, tmp_path):
        seen_local, seen_ssh = _run(tmp_path, {})
        assert seen_local == "~/.ansible/tmp"
        assert seen_ssh == "~/.ansible/tmp"

    def test_operator_remote_temp_still_honored(self, tmp_path):
        seen_local, seen_ssh = _run(
            tmp_path, {"ANSIBLE_REMOTE_TEMP": str(tmp_path / "op")})
        assert seen_local == str(tmp_path / "op")
        assert seen_ssh == str(tmp_path / "op")
