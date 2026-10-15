"""An unhealthy web container is reported with the startup error it logged.

MediaWiki loading an extension the image does not ship crash-loops web with a
PHP fatal error. The health gate used to time out with a generic hint about
composer and the database, leaving the actual cause buried in a log dump next
to a misleading "Unsupported database type" line and 100 lines of database
log.
"""

import os
import stat
import subprocess
import sys

import pytest
import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, REPO_ROOT)

import canasta_web_cause  # noqa: E402
from direct_commands import _helpers  # noqa: E402

ORCH_TASKS = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks")

# Logged by MediaWiki 1.43 on Canasta 3.5.28 with
# wfLoadExtension( 'GoogleAnalyticsMetrics' ) and the extension absent.
_EXT_JSON = "/var/www/mediawiki/w/extensions/GoogleAnalyticsMetrics/extension.json"
MISSING_EXTENSION_LOG = (
    "web-1  | Running composer update...\n"
    "web-1  | PHP Fatal error:  Uncaught Exception: Unable to open file "
    + _EXT_JSON + ": filemtime(): stat failed for " + _EXT_JSON
    + " in /var/www/mediawiki/w/includes/registration/ExtensionRegistry.php"
    ":204\n"
    "web-1  | Error: The GoogleAnalyticsMetrics extension cannot be loaded. "
    "Check that all of its files are installed properly.\n"
    "web-1  | Unsupported database type (Error: Unsupported database type "
    "\"\")\n"
)

MISSING_SKIN_LOG = (
    "PHP Fatal error:  Uncaught Exception: Unable to open file "
    "/var/www/mediawiki/w/skins/Chameleon/skin.json: filemtime(): stat "
    "failed\n"
)

FATAL_LOG = """\
PHP Fatal error:  Uncaught Error: Class "Foo\\\\Bar" not found in /var/www/mediawiki/w/LocalSettings.php:12
"""

HEALTHY_LOOKING_LOG = """\
web-1  | Running composer update...
web-1  | AH00558: apache2: Could not reliably determine the server's name
"""


class TestWebStartupCause:
    def test_names_the_missing_extension(self):
        cause = canasta_web_cause.web_startup_cause(
            MISSING_EXTENSION_LOG, "mysite")
        assert cause["cause"] == (
            "MediaWiki can't load extension 'GoogleAnalyticsMetrics': its "
            "files aren't in the image or the instance's extensions/ "
            "directory.")
        assert ("canasta extension disable GoogleAnalyticsMetrics -i mysite"
                in cause["hint"])
        assert "extensions/ directory" in cause["hint"]

    def test_the_fatal_error_alone_names_the_extension(self):
        only_fatal = MISSING_EXTENSION_LOG.splitlines()[1]
        cause = canasta_web_cause.web_startup_cause(only_fatal, "mysite")
        assert "extension 'GoogleAnalyticsMetrics'" in cause["cause"]

    def test_the_cannot_be_loaded_line_alone_names_the_extension(self):
        only_error = MISSING_EXTENSION_LOG.splitlines()[2]
        cause = canasta_web_cause.web_startup_cause(only_error, "mysite")
        assert "extension 'GoogleAnalyticsMetrics'" in cause["cause"]

    def test_names_a_missing_skin(self):
        cause = canasta_web_cause.web_startup_cause(MISSING_SKIN_LOG, "mysite")
        assert "skin 'Chameleon'" in cause["cause"]
        assert "skins/ directory" in cause["cause"]
        assert "canasta skin disable Chameleon -i mysite" in cause["hint"]

    def test_a_missing_extension_outranks_a_composer_failure(self):
        cause = canasta_web_cause.web_startup_cause(
            MISSING_EXTENSION_LOG, "mysite", "Your requirements could not "
            "be resolved to an installable set of packages.")
        assert "GoogleAnalyticsMetrics" in cause["cause"]

    def test_a_composer_failure_outranks_a_bare_fatal_error(self):
        cause = canasta_web_cause.web_startup_cause(
            FATAL_LOG, "mysite", "Your requirements could not be resolved.\n")
        assert cause["cause"] == (
            "The web container's startup composer update failed: Your "
            "requirements could not be resolved.")
        assert "canasta restart -i mysite" in cause["hint"]

    def test_reports_a_bare_php_fatal_error(self):
        cause = canasta_web_cause.web_startup_cause(FATAL_LOG, "mysite")
        assert cause["cause"].startswith(
            "MediaWiki failed to start with a PHP fatal error: Uncaught "
            "Error: Class")

    def test_a_long_fatal_error_is_cut_short(self):
        cause = canasta_web_cause.web_startup_cause(
            "PHP Fatal error: " + "x" * 1000, "mysite")
        assert len(cause["cause"]) < 400

    def test_nothing_recognized_is_empty(self):
        assert canasta_web_cause.web_startup_cause(
            HEALTHY_LOOKING_LOG, "mysite") == {}
        assert canasta_web_cause.web_startup_cause(None, "mysite") == {}

    def test_the_filter_plugin_exposes_it(self):
        sys.path.insert(0, os.path.join(REPO_ROOT, "filter_plugins"))
        import canasta_containers
        filters = canasta_containers.FilterModule().filters()
        assert filters["web_startup_cause"](
            MISSING_EXTENSION_LOG, "x")["cause"].startswith(
                "MediaWiki can't load extension")


DOCKER = {"path": "/srv/test", "orchestrator": "compose"}


class TestFastPathHealthGate:
    @pytest.fixture
    def unhealthy(self, monkeypatch):
        monkeypatch.setattr(_helpers.time, "sleep", lambda s: None)
        responses = [(0, "abc123\n"), (0, "unhealthy\n")]
        monkeypatch.setattr(
            _helpers, "_runtime_capture",
            lambda inst, argv, timeout=30:
                responses.pop(0) if len(responses) > 1 else responses[0])
        monkeypatch.setattr(_helpers, "_read_instance_file",
                            lambda path, host, relpath: "")
        db_dumps = []
        monkeypatch.setattr(_helpers, "_dump_db_error_log",
                            lambda inst: db_dumps.append(inst))
        return db_dumps

    def test_leads_with_the_missing_extension(self, monkeypatch, unhealthy,
                                              capsys):
        monkeypatch.setattr(_helpers, "_web_logs",
                            lambda inst, tail: MISSING_EXTENSION_LOG)
        assert _helpers._wait_web_ready("mysite", dict(DOCKER)) == 1
        err = capsys.readouterr().err
        first = err.splitlines()[0]
        assert first == (
            "Error: MediaWiki can't load extension 'GoogleAnalyticsMetrics': "
            "its files aren't in the image or the instance's extensions/ "
            "directory.")
        assert "canasta extension disable GoogleAnalyticsMetrics -i mysite" in err
        assert "did not report healthy" in err
        assert "composer is still running" not in err
        assert "--- web logs (last 20) ---" in err
        assert "Unsupported database type" in err
        assert unhealthy == []

    def test_reads_the_composer_record_from_the_host(self, monkeypatch,
                                                     unhealthy, capsys):
        monkeypatch.setattr(_helpers, "_web_logs",
                            lambda inst, tail: HEALTHY_LOOKING_LOG)
        read = []

        def fake_read(path, host, relpath):
            read.append((path, relpath))
            return "Your requirements could not be resolved.\nmore\n"

        monkeypatch.setattr(_helpers, "_read_instance_file", fake_read)
        assert _helpers._wait_web_ready("mysite", dict(DOCKER)) == 1
        err = capsys.readouterr().err
        assert err.startswith(
            "Error: The web container's startup composer update failed: Your "
            "requirements could not be resolved.\n")
        assert read == [("/srv/test", "config/persistent/composer-update.failed")]

    def test_keeps_the_generic_hint_and_database_log_without_a_cause(
            self, monkeypatch, unhealthy, capsys):
        monkeypatch.setattr(_helpers, "_web_logs",
                            lambda inst, tail: HEALTHY_LOOKING_LOG)
        assert _helpers._wait_web_ready("mysite", dict(DOCKER)) == 1
        err = capsys.readouterr().err
        assert err.startswith(
            "Error: the 'web' container for instance 'mysite' did not report "
            "healthy within 10 minutes; its last status was 'unhealthy'.")
        assert "composer is still running or failed" in err
        assert "--- web logs (last 60) ---" in err
        assert "AH00558" in err
        assert len(unhealthy) == 1

    def test_shows_only_the_tail_of_a_long_log(self, monkeypatch, unhealthy,
                                               capsys):
        noise = "".join("web-1  | line %d\n" % i for i in range(150))
        monkeypatch.setattr(_helpers, "_web_logs",
                            lambda inst, tail: MISSING_EXTENSION_LOG + noise)
        _helpers._wait_web_ready("mysite", dict(DOCKER))
        err = capsys.readouterr().err
        assert "line 149" in err
        assert "line 129" not in err


def _executable(path, body):
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def _run_playbook(tmp_path, tasks, play_vars):
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": dict(play_vars, canasta_root=REPO_ROOT,
                     ansible_python_interpreter=sys.executable),
        "tasks": tasks,
    }]))
    inherited = {k: v for k, v in os.environ.items()
                 if not k.startswith("ANSIBLE_")}
    env = dict(inherited, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_HOME=str(tmp_path / "ansible-home"),
               ANSIBLE_FILTER_PLUGINS=os.path.join(REPO_ROOT, "filter_plugins"),
               PATH=str(tmp_path / "bin") + os.pathsep + os.environ["PATH"])
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    return subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env,
                          cwd=tmp_path)


def _start_health_gate_tasks():
    """The Compose health-gate failure block from start.yml, with its
    relative include made absolute."""
    with open(os.path.join(ORCH_TASKS, "start.yml")) as f:
        start = yaml.safe_load(f)
    compose = next(t for t in start if t["name"] == "Start (Docker Compose)")
    gate = next(t for t in compose["block"]
                if t["name"] == "Fail when web never reported healthy")
    tasks = gate["block"]
    for task in tasks:
        include = task.get("ansible.builtin.include_tasks")
        if include and not include.startswith("{{"):
            task["ansible.builtin.include_tasks"] = os.path.join(
                ORCH_TASKS, include)
    return tasks


class TestAnsibleComposeHealthGate:
    def _run(self, tmp_path, log, composer=None):
        (tmp_path / "bin").mkdir()
        logfile = tmp_path / "web.log"
        logfile.write_text(log)
        compose = _executable(tmp_path / "bin" / "fake-compose",
                              'cat "%s"\n' % logfile)
        # The database log volume does not exist.
        inspect = _executable(tmp_path / "bin" / "fake-docker", "exit 1\n")
        inst = tmp_path / "inst"
        (inst / "config" / "persistent").mkdir(parents=True)
        if composer is not None:
            (inst / "config" / "persistent" / "composer-update.failed"
             ).write_text(composer)
        return _run_playbook(tmp_path, _start_health_gate_tasks(), {
            "instance_id": "mysite",
            "instance_path": str(inst),
            "_compose_cmd": compose,
            "_compose_files": [],
            "inspect_command": inspect,
            "_start_web_health": {"stdout": "unhealthy\n"},
        })

    def test_leads_with_the_missing_extension(self, tmp_path):
        proc = self._run(tmp_path, MISSING_EXTENSION_LOG)
        out = proc.stdout + proc.stderr
        assert proc.returncode != 0
        assert ("MediaWiki can't load extension 'GoogleAnalyticsMetrics'"
                in out), out
        assert "canasta extension disable GoogleAnalyticsMetrics -i mysite" in out
        assert "--- web logs (last 20) ---" in out
        assert "database error log" not in out
        assert "composer is still running" not in out

    def test_names_the_composer_record(self, tmp_path):
        proc = self._run(tmp_path, HEALTHY_LOOKING_LOG,
                         composer="Your requirements could not be resolved.\n")
        out = proc.stdout + proc.stderr
        assert proc.returncode != 0
        assert ("startup composer update failed: Your requirements could "
                "not be resolved." in out), out

    def test_keeps_the_generic_hint_without_a_cause(self, tmp_path):
        proc = self._run(tmp_path, HEALTHY_LOOKING_LOG)
        out = proc.stdout + proc.stderr
        assert proc.returncode != 0
        assert "composer is still running or failed" in out, out
        assert "--- web logs (last 60) ---" in out
        assert "AH00558" in out
        assert "database error log (last 100)" in out


class TestK8sRolloutFailure:
    def _run(self, tmp_path, log):
        (tmp_path / "bin").mkdir()
        logfile = tmp_path / "web.log"
        logfile.write_text(log)
        _executable(tmp_path / "bin" / "kubectl",
                    'if [ "$1" = logs ]; then cat "%s"; fi\n' % logfile)
        return _run_playbook(tmp_path, [{
            "ansible.builtin.include_tasks":
                os.path.join(ORCH_TASKS, "k8s_capture_rollout_failure.yml"),
        }], {"instance_id": "mysite", "_k8s_namespace": "canasta-mysite"})

    def test_names_the_missing_extension_first(self, tmp_path):
        proc = self._run(tmp_path, MISSING_EXTENSION_LOG)
        out = proc.stdout + proc.stderr
        assert proc.returncode == 0, out
        assert "MediaWiki can't load extension 'GoogleAnalyticsMetrics'" in out
        assert "canasta extension disable GoogleAnalyticsMetrics -i mysite" in out
        assert out.index("can't load extension") < out.index(
            "The rollout did not finish")

    def test_says_nothing_extra_without_a_cause(self, tmp_path):
        proc = self._run(tmp_path, HEALTHY_LOOKING_LOG)
        out = proc.stdout + proc.stderr
        assert proc.returncode == 0, out
        assert "can't load" not in out
        assert "fatal error" not in out

    def test_k8s_waits_capture_the_cause(self):
        """Every K8s web rollout wait reports why it failed."""
        for path, wait in (
                (os.path.join(ORCH_TASKS, "start.yml"),
                 "Wait for web readiness"),
                (os.path.join(REPO_ROOT, "roles", "upgrade", "tasks",
                              "main.yml"),
                 "Wait for the web pods to run the rebuilt image")):
            with open(path) as f:
                text = f.read()
            section = text[text.index("name: " + wait):]
            section = section[:section.index("Fail with the original")]
            assert "rescue:" in section
            assert "k8s_capture_rollout_failure.yml" in section
