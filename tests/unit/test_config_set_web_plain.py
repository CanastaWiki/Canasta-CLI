"""`config set --web` without --secret passes a plain setting to the web
container.

The value stays a plain .env setting (an env.template literal on a gitops
instance, shown by `config get`), and the key is listed in the tracked
config/env-keys-web, which renders into docker-compose.web-env.yml.
`config unset` takes the key off the list.
"""

import os
import shutil
import subprocess
import sys

import yaml

import canasta_render_web_env
from mock_ansible import run_module_with_params

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CONFIG_TASKS = os.path.join(REPO_ROOT, "roles", "config", "tasks")
GITOPS_TASKS = os.path.join(REPO_ROOT, "roles", "gitops", "tasks")
CLASSIFICATION = os.path.join(REPO_ROOT, "vars", "secret_classification.yml")
CONFIG_DEFAULTS = os.path.join(REPO_ROOT, "roles", "config", "defaults", "main.yml")
GITIGNORE = os.path.join(REPO_ROOT, "roles", "gitops", "files", "gitignore.default")

KEY = "MW_MAP_DOMAIN_TO_DOCKER_GATEWAY"
OVERLAY = "docker-compose.web-env.yml"


def _read(path):
    with open(path) as f:
        return f.read()


def _load(path):
    return yaml.safe_load(_read(path))


def _instance(tmp_path, env="", lists=None):
    """A fresh instance directory; a second call starts it over."""
    inst = tmp_path / "inst"
    if inst.exists():
        shutil.rmtree(inst)
    (inst / "config").mkdir(parents=True)
    (inst / ".env").write_text(env)
    for name, content in (lists or {}).items():
        (inst / name).write_text(content)
    return inst


def _play(tmp_path, inst, tasks, extra=None):
    """Run `tasks` (a task file path, or a list of task dicts) on localhost
    with the config defaults and the secret classification loaded."""
    if isinstance(tasks, str):
        tasks = [{"ansible.builtin.include_tasks": tasks}]
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": dict({"instance_path": str(inst), "canasta_root": REPO_ROOT},
                     **(extra or {})),
        "tasks": [
            {"ansible.builtin.include_vars": {"file": CONFIG_DEFAULTS}},
            {"ansible.builtin.include_vars": {"file": CLASSIFICATION}},
        ] + tasks,
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_CONFIG=os.path.join(REPO_ROOT, "ansible.cfg"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    return subprocess.run(
        [playbook, "-i", "localhost,", str(play)],
        capture_output=True, text=True, env=env, cwd=REPO_ROOT)


def _render(inst):
    result, failed, msg = run_module_with_params(
        canasta_render_web_env, {"instance_path": str(inst)})
    assert not failed, msg
    path = inst / OVERLAY
    overlay = (yaml.safe_load(path.read_text())["services"]["web"]["environment"]
               if path.exists() else None)
    return result, overlay


class TestComposeRender:
    def test_plain_web_keys_are_passed(self, tmp_path):
        inst = _instance(tmp_path, env="%s=false\n" % KEY,
                         lists={"config/env-keys-web": KEY + "\n"})
        _, overlay = _render(inst)
        assert overlay == ["%s=${%s}" % (KEY, KEY)]

    def test_both_lists_are_passed_once_each(self, tmp_path):
        inst = _instance(
            tmp_path, env="MAPS_API_KEY=v\n%s=false\nPM_MAX_CHILDREN=12\n" % KEY,
            lists={"config/secret-keys-web": "MAPS_API_KEY\n",
                   "config/env-keys-web": "%s\nMAPS_API_KEY\n" % KEY})
        _, overlay = _render(inst)
        assert overlay == ["MAPS_API_KEY=${MAPS_API_KEY}",
                           "%s=${%s}" % (KEY, KEY),
                           "PM_MAX_CHILDREN=${PM_MAX_CHILDREN}"]

    def test_missing_values_name_the_flags_that_set_them(self, tmp_path):
        inst = _instance(tmp_path, lists={
            "config/secret-keys-web": "MAPS_API_KEY\n",
            "config/env-keys-web": KEY + "\n"})
        result, overlay = _render(inst)
        assert overlay is None
        assert result["missing"] == ["MAPS_API_KEY", KEY]
        assert "MAPS_API_KEY; set it with 'canasta config set --secret --web" in (
            result["message"])
        assert "%s; set it with 'canasta config set --web KEY=VALUE'" % KEY in (
            result["message"])

    def test_invalid_names_are_refused(self, tmp_path):
        inst = _instance(tmp_path, lists={"config/env-keys-web": "bad-name\n"})
        _, failed, msg = run_module_with_params(
            canasta_render_web_env, {"instance_path": str(inst)})
        assert failed and "config/env-keys-web" in msg and "bad-name" in msg


class TestSetChecks:
    def _check(self, tmp_path, settings, lists=None):
        inst = _instance(tmp_path, lists=lists)
        return _play(tmp_path, inst, os.path.join(CONFIG_TASKS, "_check_web_env.yml"),
                     {"_config_settings": settings})

    def test_an_app_key_is_accepted(self, tmp_path):
        proc = self._check(tmp_path, ["%s=false" % KEY])
        assert proc.returncode == 0, proc.stdout + proc.stderr

    def test_recognized_keys_are_refused(self, tmp_path):
        for setting in ("PHP_MEMORY_LIMIT=512M", "AWS_REGION=x"):
            proc = self._check(tmp_path, [setting])
            assert proc.returncode != 0, setting
            assert "recognized configuration keys" in proc.stdout + proc.stderr, setting

    def test_secret_looking_keys_are_refused(self, tmp_path):
        proc = self._check(tmp_path, ["MAPS_API_KEY=abc"])
        assert proc.returncode != 0
        assert "Pass --secret --web" in proc.stdout + proc.stderr
        assert "abc" not in proc.stdout + proc.stderr

    def test_keys_set_with_secret_are_refused(self, tmp_path):
        proc = self._check(tmp_path, ["APP_MODE=on"],
                           lists={"config/secret-keys": "APP_MODE\n"})
        assert proc.returncode != 0
        assert "config unset --secret APP_MODE" in proc.stdout + proc.stderr

    def test_invalid_names_are_refused(self, tmp_path):
        proc = self._check(tmp_path, ["bad-name=1"])
        assert proc.returncode != 0 and "bad-name" in proc.stdout + proc.stderr

    def test_set_yml_runs_the_checks_instead_of_requiring_secret(self):
        body = _read(os.path.join(CONFIG_TASKS, "set.yml"))
        assert "only applies to secret values" not in body
        assert "_check_web_env.yml" in body

    def test_secret_refuses_a_plain_web_key(self, tmp_path):
        inst = _instance(tmp_path, lists={"config/env-keys-web": "APP_MODE\n"})
        proc = _play(tmp_path, inst, os.path.join(CONFIG_TASKS, "_set_secret.yml"),
                     {"_config_settings": ["APP_MODE=on"], "web": True})
        assert proc.returncode != 0
        assert "'canasta config unset APP_MODE'" in proc.stdout + proc.stderr


class TestValidateKey:
    def _validate(self, tmp_path, extra):
        inst = _instance(tmp_path)
        return _play(tmp_path, inst, os.path.join(CONFIG_TASKS, "_validate_key.yml"),
                     dict({"_config_key": "APP_MODE", "_config_value": "on"},
                          **extra))

    def test_unrecognized_key_still_needs_force(self, tmp_path):
        proc = self._validate(tmp_path, {})
        assert proc.returncode != 0 and "Unrecognized key" in proc.stdout + proc.stderr

    def test_web_accepts_an_unrecognized_key(self, tmp_path):
        proc = self._validate(tmp_path, {"web": True})
        assert proc.returncode == 0, proc.stdout + proc.stderr

    def test_a_listed_web_key_is_accepted(self, tmp_path):
        proc = self._validate(tmp_path, {"_config_web_env_keys": ["APP_MODE"]})
        assert proc.returncode == 0, proc.stdout + proc.stderr


def _tasks_named(path, *names):
    tasks = {t["name"]: t for t in _load(path)}
    return [tasks[n] for n in names]


class TestSetAndUnsetLists:
    def test_set_records_and_stages_the_list(self):
        names = [t["name"] for t in _load(os.path.join(CONFIG_TASKS, "_set_config.yml"))]
        assert names.index("Read the plain web keys") < names.index(
            "Validate, apply side effects, and set each key")
        assert names.index("Record the plain web keys") < names.index(
            "Regenerate Caddyfile after config change")
        assert names.index("Stage the plain web key list") < names.index(
            "Regenerate Caddyfile after config change")

    def test_set_records_plain_keys_only(self, tmp_path):
        inst = _instance(tmp_path)
        tasks = _tasks_named(os.path.join(CONFIG_TASKS, "_set_config.yml"),
                             "Record the plain web keys")
        for secret, expected in ((True, False), (False, True)):
            proc = _play(tmp_path, inst, tasks,
                         {"_config_settings": ["%s=false" % KEY, "%s=x" % KEY],
                          "web": True, "secret": secret})
            assert proc.returncode == 0, proc.stdout + proc.stderr
            listed = inst / "config" / "env-keys-web"
            assert listed.exists() is expected
        assert (inst / "config" / "env-keys-web").read_text() == KEY + "\n"

    def test_unset_takes_listed_keys_off_the_list(self, tmp_path):
        inst = _instance(tmp_path, lists={
            "config/env-keys-web": "%s\nOTHER_KEY\n" % KEY})
        tasks = ([{"ansible.builtin.include_tasks":
                   os.path.join(CONFIG_TASKS, "_read_web_env_keys.yml")}]
                 + _tasks_named(os.path.join(CONFIG_TASKS, "_unset_config.yml"),
                                "Stop passing the keys to the web container"))
        proc = _play(tmp_path, inst, tasks, {"keys": "%s PHP_MEMORY_LIMIT" % KEY})
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert (inst / "config" / "env-keys-web").read_text() == "OTHER_KEY\n"

    def test_unset_reads_the_list_before_validating(self):
        names = [t["name"] for t in _load(os.path.join(CONFIG_TASKS, "_unset_config.yml"))]
        assert names.index("Read the plain web keys") < names.index(
            "Validate, unapply, and remove each key")
        assert "Stage the plain web key list" in names


class TestGitops:
    def test_list_is_tracked(self):
        assert "config/env-keys-web" not in _read(GITIGNORE).splitlines()

    def test_a_pulled_list_change_restarts(self):
        assert '"env-keys-web"' in _read(
            os.path.join(GITOPS_TASKS, "_rendered_fingerprint.yml"))
