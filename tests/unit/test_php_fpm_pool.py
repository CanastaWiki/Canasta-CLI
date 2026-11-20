"""config set and config unset refuse a PHP-FPM pool PHP-FPM would reject.

PHP-FPM refuses to start, and the wiki answers 503, when the PM_* values
break the dynamic pool's limits. A key .env does not set takes the image
default, so lowering PM_MAX_CHILDREN alone below the default
PM_MAX_SPARE_SERVERS is the common way to get there.
"""

import os
import subprocess
import sys

import pytest
import yaml

import canasta_php_fpm as rules

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ROLES = os.path.join(REPO_ROOT, "roles")
COMMON = os.path.join(ROLES, "common")
DEFAULTS = os.path.join(ROLES, "config", "defaults", "main.yml")
SET_CONFIG = os.path.join(ROLES, "config", "tasks", "_set_config.yml")
UNSET_CONFIG = os.path.join(ROLES, "config", "tasks", "_unset_config.yml")


def _load_filters():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_php_fpm_filters", os.path.join(REPO_ROOT, "filter_plugins", "canasta_php_fpm.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


filters = _load_filters()


def _known_keys():
    with open(DEFAULTS) as f:
        return yaml.safe_load(f)["canasta_known_keys"]


DEFAULTS_BY_KEY = {k["name"]: k["default"] for k in _known_keys()
                   if k["name"] in rules.POOL_KEYS}


class TestPoolError:
    def test_defaults_are_a_valid_pool(self):
        assert rules.pool_error({}, DEFAULTS_BY_KEY) == ""

    def test_lowering_max_children_alone_is_refused(self):
        msg = rules.pool_error({"PM_MAX_CHILDREN": "12"}, DEFAULTS_BY_KEY)
        assert "PM_MAX_SPARE_SERVERS cannot be greater than PM_MAX_CHILDREN" in msg
        assert "PM_MAX_CHILDREN=12," in msg
        assert "PM_MAX_SPARE_SERVERS=15 (default)" in msg

    def test_consistent_smaller_pool_is_accepted(self):
        assert rules.pool_error({"PM_MAX_CHILDREN": "12",
                                 "PM_MAX_SPARE_SERVERS": "8",
                                 "PM_START_SERVERS": "6"}, DEFAULTS_BY_KEY) == ""

    @pytest.mark.parametrize("values, problem", [
        ({"PM_MIN_SPARE_SERVERS": "16"},
         "PM_MIN_SPARE_SERVERS cannot be greater than PM_MAX_SPARE_SERVERS"),
        ({"PM_START_SERVERS": "20"},
         "PM_START_SERVERS must be between"),
        ({"PM_START_SERVERS": "2"},
         "PM_START_SERVERS must be between"),
        ({"PM_MAX_CHILDREN": "many"}, "not a whole number"),
        ({"PM_MAX_CHILDREN": "0"}, "must be at least 1"),
    ])
    def test_invalid_pools(self, values, problem):
        assert problem in rules.pool_error(values, DEFAULTS_BY_KEY)

    def test_empty_value_takes_the_default(self):
        assert rules.pool_error({"PM_MAX_CHILDREN": ""}, DEFAULTS_BY_KEY) == ""

    def test_filter_reads_defaults_from_known_keys(self):
        assert filters.php_fpm_pool_error({}, _known_keys()) == ""
        assert filters.php_fpm_pool_error({"PM_MAX_CHILDREN": "12"},
                                          _known_keys()) != ""


def _pool_check_tasks(path):
    """The pool-check tasks at the top of a config task file."""
    with open(path) as f:
        tasks = yaml.safe_load(f)
    out = []
    for task in tasks:
        if "include_tasks" in str(task) or "ansible.builtin.include_tasks" in task:
            break
        out.append(task)
    return out


def _run(tmp_path, env_text, play_vars, tasks):
    inst = tmp_path / "inst"
    inst.mkdir()
    (inst / ".env").write_text(env_text)
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {"instance_path": str(inst), "canasta_known_keys": _known_keys(),
                 "ansible_python_interpreter": sys.executable, **play_vars},
        "tasks": tasks + [{"ansible.builtin.debug": {"msg": "POOL-CHECK-PASSED"}}],
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_STDOUT_CALLBACK="default", ANSIBLE_LOAD_CALLBACK_PLUGINS="0",
               ANSIBLE_FILTER_PLUGINS=os.path.join(REPO_ROOT, "filter_plugins"),
               ANSIBLE_LIBRARY=os.path.join(COMMON, "library"),
               ANSIBLE_MODULE_UTILS=os.path.join(COMMON, "module_utils"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env, cwd=tmp_path)
    proc.output = proc.stdout + proc.stderr
    return proc


class TestConfigSet:
    def _set(self, tmp_path, env_text, settings):
        return _run(tmp_path, env_text, {"_config_settings": settings},
                    _pool_check_tasks(SET_CONFIG))

    def test_refuses_max_children_alone(self, tmp_path):
        proc = self._set(tmp_path, "MW_SITE_SERVER=https://example.com\n",
                         ["PM_MAX_CHILDREN=12"])
        assert proc.returncode != 0
        assert "PM_MAX_SPARE_SERVERS cannot be greater" in proc.output
        assert "POOL-CHECK-PASSED" not in proc.output

    def test_accepts_keys_set_together(self, tmp_path):
        proc = self._set(tmp_path, "",
                         ["PM_MAX_CHILDREN=12", "PM_MAX_SPARE_SERVERS=8",
                          "PM_START_SERVERS=6"])
        assert proc.returncode == 0, proc.output
        assert "POOL-CHECK-PASSED" in proc.output

    def test_counts_keys_already_in_env(self, tmp_path):
        proc = self._set(tmp_path, "PM_MAX_SPARE_SERVERS=8\nPM_START_SERVERS=6\n",
                         ["PM_MAX_CHILDREN=12"])
        assert proc.returncode == 0, proc.output

    def test_other_keys_skip_the_check(self, tmp_path):
        proc = self._set(tmp_path, "PM_MAX_CHILDREN=12\n",
                         ["MW_SITEMAP_PAUSE_DAYS=2", "MYSQL_PASSWORD=a=b"])
        assert proc.returncode == 0, proc.output
        assert "a=b" not in proc.output


class TestConfigUnset:
    def test_refuses_unset_that_breaks_the_pool(self, tmp_path):
        proc = _run(tmp_path,
                    "PM_MAX_CHILDREN=12\nPM_MAX_SPARE_SERVERS=8\nPM_START_SERVERS=6\n",
                    {"keys": "PM_MAX_SPARE_SERVERS"}, _pool_check_tasks(UNSET_CONFIG))
        assert proc.returncode != 0
        assert "PM_MAX_SPARE_SERVERS=15 (default)" in proc.output

    def test_allows_unset_back_to_defaults(self, tmp_path):
        proc = _run(tmp_path, "PM_MAX_CHILDREN=12\n",
                    {"keys": "PM_MAX_CHILDREN"}, _pool_check_tasks(UNSET_CONFIG))
        assert proc.returncode == 0, proc.output
