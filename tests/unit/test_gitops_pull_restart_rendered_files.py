"""`gitops pull` reports a restart when a file a restart applies changes.

MariaDB reads my.cnf only at startup, the Caddy routes for media hosts are
rebuilt only on restart, and Caddy and the web container read the
Caddyfiles, the web secret key list and the compose override only when they
are recreated, so a pull that changes any of them must say "Restart needed"
and name the file. The decision compares what the files hold before the
pull with what they hold after it, not which files the commits touched, so
a re-apply over commits the instance already runs needs no restart. These
tests run the fingerprint helper and the restart decision from
pull_compose.yml against a temp instance.
"""

import json
import os
import subprocess
import sys

import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TASKS = os.path.join(REPO_ROOT, "roles", "gitops", "tasks")
FINGERPRINT = os.path.join(TASKS, "_rendered_fingerprint.yml")
PULL_COMPOSE = os.path.join(TASKS, "pull_compose.yml")
COMMON = os.path.join(REPO_ROOT, "roles", "common")

MEDIA = "media_hosts:\n- hostname: media.example.com\n  bucket: b\n  endpoint: e\n"


def _decision_task():
    with open(PULL_COMPOSE) as f:
        return next(t for t in yaml.safe_load(f)
                    if t.get("name") == "Determine if restart is needed")


def _instance(tmp_path):
    inst = tmp_path / "inst"
    (inst / "config").mkdir(parents=True)
    (inst / ".env").write_text("MW_SITE_SERVER=https://example.com\n")
    (inst / "config" / "wikis.yaml").write_text(
        "wikis:\n- id: main\n  url: example.com\n  name: Main\n")
    (inst / "my.cnf").write_text("[mysqld]\ninnodb_buffer_pool_size=1G\n")
    (inst / "config" / "media-hosts.yaml").write_text(MEDIA)
    return inst


def _run(tmp_path, inst, edit, baseline=None, changed=()):
    """Fingerprint, apply `edit`, fingerprint again, and name the triggers.

    `baseline` overrides the pre-render fingerprint, as a baseline left on
    disk by an unfinished pull would.
    """
    script = tmp_path / "edit.py"
    script.write_text(edit)
    out = tmp_path / "out.json"
    tasks = [
        {"ansible.builtin.include_tasks": FINGERPRINT},
        {"ansible.builtin.set_fact": {
            "_pull_baseline": ("{{ _rendered_fingerprint }}" if baseline is None
                               else baseline)}},
        {"ansible.builtin.command": {"argv": [sys.executable, str(script),
                                              str(inst)]}},
        {"ansible.builtin.include_tasks": FINGERPRINT},
        _decision_task(),
        {"ansible.builtin.copy": {
            "content": "{{ {'restart': _pull_restart_triggers} | to_json }}",
            "dest": str(out)}},
    ]
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {"instance_path": str(inst), "canasta_root": REPO_ROOT,
                 "ansible_python_interpreter": sys.executable,
                 "_pull_changed_files": {"stdout_lines": list(changed)},
                 "_pull_bridge": {"stat": {"exists": True}},
                 "_pull_sidecars": {"stat": {"exists": True}}},
        "tasks": tasks,
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_FILTER_PLUGINS=os.path.join(REPO_ROOT, "filter_plugins"),
               ANSIBLE_LIBRARY=os.path.join(COMMON, "library"),
               ANSIBLE_MODULE_UTILS=os.path.join(COMMON, "module_utils"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env, cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return json.loads(out.read_text())["restart"]


EDIT_HEAD = "import pathlib, sys\ninst = pathlib.Path(sys.argv[1])\n"


class TestRestartForRenderedFiles:
    def test_unchanged_files_need_no_restart(self, tmp_path):
        inst = _instance(tmp_path)
        assert _run(tmp_path, inst, EDIT_HEAD) == []

    def test_a_changed_my_cnf_needs_a_restart(self, tmp_path):
        inst = _instance(tmp_path)
        edit = EDIT_HEAD + (
            "(inst / 'my.cnf').write_text("
            "'[mysqld]\\ninnodb_buffer_pool_size=2G\\n')\n")
        assert _run(tmp_path, inst, edit) == ["my.cnf"]

    def test_a_changed_media_host_needs_a_restart(self, tmp_path):
        inst = _instance(tmp_path)
        edit = EDIT_HEAD + (
            "p = inst / 'config' / 'media-hosts.yaml'\n"
            "p.write_text(p.read_text().replace('media.', 'img.'))\n")
        assert _run(tmp_path, inst, edit) == ["config/media-hosts.yaml"]

    def test_a_new_media_hosts_file_needs_a_restart(self, tmp_path):
        inst = _instance(tmp_path)
        (inst / "config" / "media-hosts.yaml").unlink()
        edit = EDIT_HEAD + (
            "(inst / 'config' / 'media-hosts.yaml').write_text(%r)\n" % MEDIA)
        assert _run(tmp_path, inst, edit) == ["config/media-hosts.yaml"]

    def test_relaid_media_hosts_need_no_restart(self, tmp_path):
        inst = _instance(tmp_path)
        edit = EDIT_HEAD + (
            "(inst / 'config' / 'media-hosts.yaml').write_text("
            "'media_hosts:\\n  - endpoint: e\\n    bucket: b\\n"
            "    hostname: media.example.com\\n')\n")
        assert _run(tmp_path, inst, edit) == []

    def test_a_baseline_from_before_these_keys_is_not_a_change(self, tmp_path):
        inst = _instance(tmp_path)
        baseline = ("{{ _rendered_fingerprint | dict2items | rejectattr('key',"
                    " 'in', ['mycnf', 'media_hosts']) | items2dict }}")
        assert _run(tmp_path, inst, EDIT_HEAD, baseline=baseline) == []

    def test_a_pulled_change_to_a_tracked_my_cnf_needs_a_restart(self, tmp_path):
        inst = _instance(tmp_path)
        edit = EDIT_HEAD + (
            "(inst / 'my.cnf').write_text('[mysqld]\\nmax_connections=50\\n')\n")
        assert _run(tmp_path, inst, edit, changed=["my.cnf"]) == ["my.cnf"]

    def test_a_pulled_template_alone_needs_no_restart(self, tmp_path):
        inst = _instance(tmp_path)
        assert _run(tmp_path, inst, EDIT_HEAD,
                    changed=["my.cnf.template"]) == []

    def test_a_pulled_caddyfile_change_needs_a_restart(self, tmp_path):
        inst = _instance(tmp_path)
        (inst / "config" / "Caddyfile.site").write_text("# site\n")
        edit = EDIT_HEAD + (
            "(inst / 'config' / 'Caddyfile.site').write_text('# edited\\n')\n")
        assert _run(tmp_path, inst, edit) == ["config/Caddyfile.site"]

    def test_new_and_removed_restart_files_need_a_restart(self, tmp_path):
        inst = _instance(tmp_path)
        (inst / "docker-compose.override.yml").write_text("services: {}\n")
        edit = EDIT_HEAD + (
            "(inst / 'docker-compose.override.yml').unlink()\n"
            "(inst / 'config' / 'secret-keys-web').write_text('API_KEY\\n')\n")
        assert _run(tmp_path, inst, edit) == [
            "config/secret-keys-web", "docker-compose.override.yml"]

    def test_a_reapply_over_changes_already_running_needs_no_restart(self, tmp_path):
        """The range since a stale .gitops-applied names restart files, but
        the instance already runs them: nothing differs from the baseline."""
        inst = _instance(tmp_path)
        (inst / "config" / "Caddyfile.site").write_text("# site\n")
        changed = ["env.template", "config/Caddyfile.site",
                   "config/secret-keys-web", "docker-compose.override.yml",
                   "my.cnf"]
        assert _run(tmp_path, inst, EDIT_HEAD, changed=changed) == []

    def test_a_baseline_from_before_file_fingerprints_is_not_a_change(self, tmp_path):
        inst = _instance(tmp_path)
        baseline = ("{{ _rendered_fingerprint | dict2items | rejectattr('key',"
                    " 'eq', 'files') | items2dict }}")
        edit = EDIT_HEAD + (
            "(inst / 'config' / 'Caddyfile.site').write_text('# new\\n')\n")
        assert _run(tmp_path, inst, edit, baseline=baseline) == []

    def test_a_changed_env_is_named(self, tmp_path):
        inst = _instance(tmp_path)
        edit = EDIT_HEAD + (
            "(inst / '.env').write_text('MW_SITE_SERVER=https://b.example.com\\n')\n")
        assert _run(tmp_path, inst, edit) == [".env"]
