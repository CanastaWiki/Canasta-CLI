"""The vars file canasta.py writes for each run (config set values, secrets
included) is removed once the play has loaded it."""

import json
import os
import subprocess
import sys

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
sys.path.insert(0, REPO_ROOT)

import canasta


def _load(*parts):
    with open(os.path.join(REPO_ROOT, *parts)) as f:
        return yaml.safe_load(f)


def _named(tasks, name):
    return next(t for t in tasks if t.get("name") == name)


class TestVarsFileRemoved:
    """config set values (secrets included) must not outlive the run."""

    def test_build_ansible_args_names_its_vars_file(self, tmp_path,
                                                     monkeypatch):
        monkeypatch.setenv("CANASTA_CONFIG_DIR", str(tmp_path))

        class _A:
            def __getattr__(self, name):
                return None
        args = _A()
        args.verbose = False
        argv = canasta.build_ansible_args(
            "ansible-playbook", "version", args, _load("meta",
                                                       "command_definitions.yml"))
        path = argv[argv.index("-e") + 1].lstrip("@")
        try:
            with open(path) as f:
                assert json.load(f)["canasta_vars_file"] == path
        finally:
            os.unlink(path)

    def test_canasta_yml_removes_it_once_loaded(self, tmp_path):
        pre = _load("canasta.yml")[0]["pre_tasks"]
        tasks = [_named(pre, "Add the controller as a delegation target"),
                 _named(pre, "Remove the vars file canasta.py wrote")]
        vars_file = tmp_path / "canasta-vars-test.json"
        vars_file.write_text(json.dumps({
            "canasta_vars_file": str(vars_file), "secret": "s3cret"}))
        play = tmp_path / "play.yml"
        play.write_text(yaml.safe_dump([{
            "hosts": "localhost", "connection": "local",
            "gather_facts": False,
            "vars": {"ansible_python_interpreter": sys.executable},
            "tasks": tasks + [{"ansible.builtin.debug": {
                "msg": "still have {{ secret }}"}}],
        }]))
        playbook = os.path.join(os.path.dirname(sys.executable),
                                "ansible-playbook")
        proc = subprocess.run(
            [playbook, "-i", "localhost,", str(play), "-e", "@%s" % vars_file],
            capture_output=True, text=True, cwd=tmp_path, timeout=120,
            env=dict(os.environ, ANSIBLE_NOCOLOR="1"))
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert not vars_file.exists()
        # The vars were loaded before the file went.
        assert "still have s3cret" in proc.stdout
