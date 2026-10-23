"""A .env value reaches the containers as it was set.

Docker Compose expands `$` in a bare .env value, so `config set
'K=ab$cd=ef'` once gave the containers `ab=ef`. Values Compose would change
are now written single-quoted, and every reader of .env returns the
original value.
"""
import json
import os
import shutil
import subprocess
import sys

import pytest

import canasta_env
import canasta_env_quote
import direct_commands
from mock_ansible import run_module_with_params

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO_ROOT, "filter_plugins"))
import canasta_dotenv  # noqa: E402
import canasta_gitops  # noqa: E402

PROBE = os.path.join(REPO_ROOT, "roles", "upgrade", "files",
                     "probe_migration_state.py")

VALUES = {
    "DOLLAR": "ab$cd=ef",
    "BRACES": "x${HOME}y",
    "DOUBLE_DOLLAR": "a$$b",
    "QUOTE": "it's $5",
    "DQUOTE": '"quoted"',
    "SPACES": "  two words  ",
    "HASH": "a #b",
    "LEADING_HASH": "#x",
    "BACKSLASH": "a\\b$c",
    "EQUALS": "k=v=w",
    "PLAIN": "plain-value_1",
}


def _set_all(path):
    for key, value in VALUES.items():
        _, failed, _ = run_module_with_params(canasta_env, {
            "path": path, "state": "set", "key": key, "value": value,
            "keys": None,
        })
        assert not failed


@pytest.fixture
def env_path(tmp_path):
    path = tmp_path / ".env"
    _set_all(str(path))
    return path


class TestQuoteValue:
    @pytest.mark.parametrize("value", sorted(VALUES.values()))
    def test_round_trip(self, value):
        assert canasta_env_quote.unquote_value(
            canasta_env_quote.quote_value(value)) == value

    @pytest.mark.parametrize("value", ["plain", "a=b", "it's", "a\\b",
                                       "a#b", "two words", ""])
    def test_values_compose_reads_as_is_stay_bare(self, value):
        assert canasta_env_quote.quote_value(value) == value

    def test_dollar_is_single_quoted(self):
        assert canasta_env_quote.quote_value("ab$cd") == "'ab$cd'"

    def test_quote_is_escaped(self):
        assert canasta_env_quote.quote_value("it's $5") == "'it\\'s $5'"

    @pytest.mark.parametrize("value", ["a$\\'b", "a$b\\"])
    def test_unrepresentable_value_is_refused(self, value):
        with pytest.raises(ValueError):
            canasta_env_quote.quote_value(value)


class TestModuleRoundTrip:
    def test_written_lines(self, env_path):
        lines = env_path.read_text().splitlines()
        assert "DOLLAR='ab$cd=ef'" in lines
        assert "PLAIN=plain-value_1" in lines
        assert "EQUALS=k=v=w" in lines

    def test_read_all_returns_the_values(self, env_path):
        result, _, _ = run_module_with_params(canasta_env, {
            "path": str(env_path), "state": "read_all", "key": None,
            "value": None, "keys": None,
        })
        assert {k: result["variables"][k] for k in VALUES} == VALUES

    def test_setting_again_is_idempotent(self, env_path):
        result, _, _ = run_module_with_params(canasta_env, {
            "path": str(env_path), "state": "set", "key": "DOLLAR",
            "value": VALUES["DOLLAR"], "keys": None,
        })
        assert not result["changed"]

    def test_unrepresentable_value_fails_the_set(self, tmp_path):
        result, failed, _ = run_module_with_params(canasta_env, {
            "path": str(tmp_path / ".env"), "state": "set", "key": "K",
            "value": "a$b\\", "keys": None,
        })
        assert failed
        assert "cannot be stored" in result["msg"]


class TestDirectCommandsRoundTrip:
    def test_helpers_write_what_the_module_writes(self, env_path, tmp_path):
        lines = [""]
        for key, value in VALUES.items():
            lines = direct_commands._helpers._set_env_lines(lines, key, value)
        assert "\n".join(lines).strip() == env_path.read_text().strip()

    def test_read_env_file(self, env_path):
        env = direct_commands._read_env_file(str(env_path.parent), "localhost")
        assert {k: env[k] for k in VALUES} == VALUES

    def test_read_env(self, env_path):
        for key, value in VALUES.items():
            assert direct_commands._helpers._read_env(
                str(env_path.parent), key) == value


class TestGitops:
    TEMPLATE = "".join("%s={{%s}}\n" % (k, k.lower()) for k in VALUES)

    def test_render_writes_what_the_module_writes(self, env_path):
        rendered = canasta_gitops.canasta_env_render(
            self.TEMPLATE, {k.lower(): v for k, v in VALUES.items()})
        assert rendered.rstrip("\n") == env_path.read_text().rstrip("\n")

    def test_sync_plan_sees_no_edit(self, env_path):
        plan = canasta_gitops.canasta_env_sync_plan(
            env_path.read_text(), self.TEMPLATE,
            {k.lower(): v for k, v in VALUES.items()})
        assert plan == {"set": {}, "unset": [], "conflict": []}

    def test_sync_plan_carries_the_value_not_its_quoting(self, env_path):
        template = self.TEMPLATE.replace("DOLLAR={{dollar}}", "DOLLAR=old")
        plan = canasta_gitops.canasta_env_sync_plan(
            env_path.read_text(), template,
            {k.lower(): v for k, v in VALUES.items()},
            base_template=template,
            base_vars={k.lower(): v for k, v in VALUES.items()})
        assert plan["set"] == {"DOLLAR": "ab$cd=ef"}

    def test_bare_literal_with_dollar_is_quoted_on_render(self):
        assert canasta_gitops.canasta_env_render(
            "A=ab$cd\nB=plain", {}) == "A='ab$cd'\nB=plain\n"

    def test_reference_to_another_key_is_kept(self):
        template = "FQDN=example.com\nURL=https://${FQDN}"
        assert canasta_gitops.canasta_env_render(template, {}) == (
            template + "\n")


@pytest.mark.skipif(shutil.which("sed") is None, reason="needs sed")
class TestShellReaders:
    def test_decoded_env_file(self, env_path):
        out = subprocess.run(
            ["sh", "-c", canasta_env_quote.decode_sed_command(str(env_path))],
            capture_output=True, text=True, check=True).stdout
        decoded = dict(line.split("=", 1) for line in out.splitlines())
        assert {k: decoded[k] for k in VALUES} == VALUES

    def test_dotenv_exec_exports_the_values(self, env_path):
        names = {"V_" + k: k for k in VALUES}
        prefix = canasta_dotenv.canasta_dotenv_exec(names)
        out = subprocess.run(["sh", "-c", prefix + " env"],
                             cwd=str(env_path.parent), capture_output=True,
                             text=True, check=True).stdout
        got = dict(line[2:].split("=", 1) for line in out.splitlines()
                   if line.startswith("V_"))
        assert got == VALUES

    def test_restic_task_passes_the_decoded_file(self, tmp_path):
        import jinja2
        with open(os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks",
                               "run_backup.yml")) as f:
            text = f.read()
        line = next(l for l in text.splitlines() if "--env-file" in l
                    and "{{" in l)
        env = jinja2.Environment()
        env.filters["canasta_dotenv_decoded"] = (
            canasta_dotenv.canasta_dotenv_decoded)
        rendered = env.from_string(line).render(instance_path="/srv/i")
        assert rendered.strip() == "--env-file <(%s) \\" % (
            canasta_env_quote.decode_sed_command("/srv/i/.env"))

    @pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
    def test_env_file_substitution(self, env_path):
        sub = canasta_dotenv.canasta_dotenv_decoded(str(env_path))
        out = subprocess.run(["bash", "-c", "cat " + sub],
                             capture_output=True, text=True, check=True).stdout
        assert "DOLLAR=ab$cd=ef" in out.splitlines()


class TestMigration:
    def _requote(self, tmp_path, content):
        path = tmp_path / ".env"
        path.write_text(content)
        result, failed, _ = run_module_with_params(canasta_env, {
            "path": str(path), "state": "requote", "key": None,
            "value": None, "keys": None,
        })
        assert not failed
        return result, path.read_text()

    def test_bare_dollar_is_quoted(self, tmp_path):
        result, text = self._requote(
            tmp_path, "A=plain\nOIDC_SECRET=ab$cd=ef\n# x$y\n")
        assert text == "A=plain\nOIDC_SECRET='ab$cd=ef'\n# x$y\n"
        assert result["changed"]
        assert result["requoted_keys"] == ["OIDC_SECRET"]

    def test_double_dollar_keeps_what_compose_passed(self, tmp_path):
        _, text = self._requote(tmp_path, "A=a$$b$c\n")
        assert text == "A='a$b$c'\n"

    def test_escaped_only_value_is_left(self, tmp_path):
        # Compose already passes `$$` through as `$`, as for the
        # observability password hash the CLI writes escaped.
        content = "OS_PASSWORD_HASH=$$2b$$12$$abc\n"
        result, text = self._requote(tmp_path, content)
        assert text == content
        assert not result["changed"]

    def test_reference_to_a_defined_key_is_left(self, tmp_path):
        content = "FQDN=example.com\nURL=https://${FQDN}/$FQDN\n"
        result, text = self._requote(tmp_path, content)
        assert text == content
        assert not result["changed"]
        assert result["conflict_keys"] == []

    def test_mixed_reference_is_reported(self, tmp_path):
        content = "FQDN=example.com\nURL=${FQDN}$x\n"
        result, text = self._requote(tmp_path, content)
        assert text == content
        assert result["conflict_keys"] == ["URL"]

    def test_clean_file_is_untouched(self, tmp_path):
        content = "A=1\nB='q$'\nC=\"d\"\n"
        result, text = self._requote(tmp_path, content)
        assert text == content
        assert not result["changed"]

    def test_probe_flags_only_bare_dollar(self, tmp_path):
        def probe(content):
            (tmp_path / ".env").write_text(content)
            out = subprocess.run([sys.executable, PROBE, str(tmp_path)],
                                 capture_output=True, text=True, check=True)
            return json.loads(out.stdout)["files"]["env_has_bare_dollar"]
        assert probe("A=ab$cd\n") is True
        assert probe("A='ab$cd'\nB=x$$y\n# C=$y\n") is False

    def test_migration_is_in_the_upgrade_loop(self):
        with open(os.path.join(REPO_ROOT, "roles", "upgrade", "tasks",
                               "main.yml")) as f:
            assert "- quote_env_dollar_values.yml" in f.read()


def _compose_available():
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "compose", "version"],
                          capture_output=True).returncode == 0


@pytest.mark.skipif(not _compose_available(), reason="needs docker compose")
class TestCompose:
    def test_rendered_config_holds_the_values(self, env_path):
        compose = env_path.parent / "docker-compose.yml"
        compose.write_text(
            "services:\n  web:\n    image: alpine\n    environment:\n"
            + "".join("      - %s=${%s}\n" % (k, k) for k in VALUES))
        out = subprocess.run(
            ["docker", "compose", "config", "--format", "json"],
            cwd=str(env_path.parent), capture_output=True, text=True,
            check=True).stdout
        environment = json.loads(out)["services"]["web"]["environment"]
        # Compose escapes a literal `$` in its rendered output as `$$`.
        got = {k: v.replace("$$", "$") for k, v in environment.items()}
        assert got == VALUES
