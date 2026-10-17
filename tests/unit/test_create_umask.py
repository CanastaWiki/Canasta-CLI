"""Instance files the containers read must not inherit the operator's umask.

The web container reads config/ as www-data, not as the operator who ran
`canasta create`. Under a hardened umask (027, 077) anything written with the
default mode is unreadable there: an unreadable config/wikis.yaml makes
MediaWiki skip the farm config and fail every request.
"""

import contextlib
import os
import stat
import subprocess
import sys

import pytest
import yaml

import canasta_render_sidecars
import canasta_settings_yaml
import canasta_wikis_yaml
from mock_ansible import run_module_with_params

REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", ".."))
CREATE = os.path.join(REPO_ROOT, "roles", "create", "tasks", "main.yml")
COMMON = os.path.join(REPO_ROOT, "roles", "common")


@contextlib.contextmanager
def _umask(mask):
    old = os.umask(mask)
    try:
        yield
    finally:
        os.umask(old)


def _mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


def _unreadable(root):
    """Paths under root that a user other than the owner cannot read."""
    bad = []
    for dirpath, dirnames, filenames in os.walk(root):
        if _mode(dirpath) & 0o005 != 0o005:
            bad.append("%s %o" % (dirpath, _mode(dirpath)))
        for name in filenames:
            path = os.path.join(dirpath, name)
            if _mode(path) & 0o004 == 0:
                bad.append("%s %o" % (path, _mode(path)))
    return bad


def _create_tasks(names):
    with open(CREATE) as f:
        found = {t.get("name"): t for t in yaml.safe_load(f)}
    return [found[n] for n in names]


class TestCreateUnderRestrictiveUmask:
    def test_stack_files_template_and_wikis_yaml_are_readable(self, tmp_path):
        inst = tmp_path / "inst"
        play = tmp_path / "play.yml"
        play.write_text(yaml.safe_dump([{
            "hosts": "localhost",
            "connection": "local",
            "gather_facts": False,
            "vars": {"canasta_root": REPO_ROOT,
                     "instance_path": str(inst),
                     "instance_is_k8s": False,
                     "wiki": "main",
                     "_domain_with_port": "wiki.example.com",
                     "ansible_python_interpreter": sys.executable},
            "tasks": (
                _create_tasks(["Create instance directory"])
                + [{"ansible.builtin.include_role": {
                    "name": "orchestrator",
                    "tasks_from": "write_stack_files.yml"}}]
                + _create_tasks(["Copy instance template",
                                 "Generate wikis.yaml"])),
        }]))
        env = dict(os.environ, ANSIBLE_NOCOLOR="1",
                   ANSIBLE_LOCALHOST_WARNING="0",
                   ANSIBLE_ROLES_PATH=os.path.join(REPO_ROOT, "roles"),
                   ANSIBLE_FILTER_PLUGINS=os.path.join(
                       REPO_ROOT, "filter_plugins"),
                   ANSIBLE_LIBRARY=os.path.join(COMMON, "library"),
                   ANSIBLE_MODULE_UTILS=os.path.join(COMMON, "module_utils"))
        playbook = os.path.join(
            os.path.dirname(sys.executable), "ansible-playbook")
        proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                              capture_output=True, text=True, env=env,
                              cwd=tmp_path,
                              preexec_fn=lambda: os.umask(0o077))
        assert proc.returncode == 0, proc.stdout + proc.stderr

        assert (inst / "config" / "wikis.yaml").is_file()
        assert (inst / "config" / "logstash").is_dir()
        assert _unreadable(inst) == []


class TestModulesUnderRestrictiveUmask:
    def test_generated_wikis_yaml(self, tmp_path):
        with _umask(0o077):
            _, failed, msg = run_module_with_params(canasta_wikis_yaml, {
                "instance_path": str(tmp_path), "state": "generate",
                "wiki_id": "main", "domain": "wiki.example.com",
                "site_name": ""})
        assert not failed, msg
        assert _mode(tmp_path / "config") == 0o755
        assert _mode(tmp_path / "config" / "wikis.yaml") == 0o644

    def test_an_unreadable_wikis_yaml_is_repaired_on_write(self, tmp_path):
        (tmp_path / "config").mkdir()
        path = tmp_path / "config" / "wikis.yaml"
        path.write_text("wikis: []\n")
        path.chmod(0o600)
        canasta_wikis_yaml.write_wikis(str(tmp_path), [])
        assert _mode(path) == 0o644

    def test_settings_yaml(self, tmp_path):
        path = canasta_settings_yaml.config_path(str(tmp_path), "main")
        with _umask(0o077):
            canasta_settings_yaml.write_config(
                path, {"extensions": ["Cite"]})
        assert _mode(path) == 0o644
        assert _mode(os.path.dirname(path)) == 0o755

    @pytest.mark.parametrize("existing", [None, "<?php\n"])
    def test_sidecar_env_bridge(self, tmp_path, existing):
        path = tmp_path / "00-canasta-sidecar-env.php"
        if existing is not None:
            path.write_text(existing)
            path.chmod(0o600)
        with _umask(0o077):
            canasta_render_sidecars.write_or_remove(
                str(path), "<?php\n", False, mode=0o644)
        assert _mode(path) == 0o644


def _git(cwd, *args):
    subprocess.run(
        ["git", "-c", "protocol.file.allow=always", "-c", "user.name=t",
         "-c", "user.email=t@example.invalid", *args],
        cwd=cwd, check=True, capture_output=True, text=True)


class TestGitCheckoutUnderRestrictiveUmask:
    def test_submodule_update_leaves_the_extension_readable(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        _git(src, "init", "-q", "-b", "main")
        (src / "extension.json").write_text("{}\n")
        _git(src, "add", ".")
        _git(src, "commit", "-q", "-m", "ext")
        top = tmp_path / "top"
        top.mkdir()
        _git(top, "init", "-q", "-b", "main")
        _git(top, "-c", "protocol.file.allow=always", "submodule", "add",
             "-q", src.as_uri(), "extensions/X")
        _git(top, "commit", "-q", "-m", "add submodule")
        clone = tmp_path / "clone"
        _git(tmp_path, "clone", "-q", str(top), str(clone))

        helper = os.path.join(
            REPO_ROOT, "roles", "gitops", "tasks", "_update_submodules.yml")
        with open(helper) as f:
            task = next(t for t in yaml.safe_load(f)
                        if t.get("name") == "Update submodules")
        env = dict(os.environ, GIT_CONFIG_COUNT="1",
                   GIT_CONFIG_KEY_0="protocol.file.allow",
                   GIT_CONFIG_VALUE_0="always")
        subprocess.run(["sh", "-c", task["ansible.builtin.shell"]["cmd"]],
                       cwd=clone, env=env, check=True, capture_output=True,
                       preexec_fn=lambda: os.umask(0o077))
        # The submodule's .git link file is git's own, not the web's.
        bad = [p for p in _unreadable(clone / "extensions" / "X")
               if not p.split()[0].endswith("/.git")]
        assert bad == []
