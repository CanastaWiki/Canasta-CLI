"""Tests for canasta.py's CLI-layer parameter validation.

These validations (required_unless, orchestrator_only, named regex
validators) run after argparse but before any Ansible work so typos fail
in milliseconds. They were extracted out of main() into testable helpers;
this exercises them directly. The playbook re-validates the same
command_definitions.yml metadata in roles/common/tasks/validate_params.yml
(the two-layer design) — TestRequiredUnlessParity guards that the two
layers stay driven by the same field.
"""

import argparse
import os
import sys

import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, REPO_ROOT)

import canasta  # noqa: E402


def _args(**kw):
    return argparse.Namespace(**kw)


class TestRequiredUnless:
    CMD = {"parameters": [
        {"name": "repo", "required_unless": "envfile"},
        {"name": "envfile"},
    ]}

    def test_neither_set_fails(self):
        errs = canasta._validate_required_unless(
            self.CMD, lambda n: getattr(_args(repo=None, envfile=None), n, None))
        assert errs == [
            (1, "Error: --repo is required unless --envfile is provided")]

    def test_partner_set_passes(self):
        vals = {"repo": None, "envfile": "/tmp/x.env"}
        errs = canasta._validate_required_unless(self.CMD, lambda n: vals.get(n))
        assert errs == []

    def test_own_set_passes(self):
        vals = {"repo": "git@example.com:r.git", "envfile": None}
        errs = canasta._validate_required_unless(self.CMD, lambda n: vals.get(n))
        assert errs == []

    def test_hyphenates_names_in_message(self):
        cmd = {"parameters": [{"name": "global_settings",
                               "required_unless": "wiki_name"}]}
        vals = {"global_settings": None, "wiki_name": None}
        errs = canasta._validate_required_unless(cmd, lambda n: vals.get(n))
        assert errs[0][1] == (
            "Error: --global-settings is required unless "
            "--wiki-name is provided")


class TestOrchestratorOnly:
    CMD = {"parameters": [
        {"name": "registry", "orchestrator_only": "kubernetes"},
    ]}

    def test_wrong_orchestrator_fails(self):
        vals = {"orchestrator": "compose", "registry": "localhost:5000"}
        errs = canasta._validate_orchestrator_only(self.CMD, lambda n: vals.get(n))
        assert errs == [
            (1, "Error: --registry can only be used with "
                "--orchestrator kubernetes")]

    def test_matching_orchestrator_passes(self):
        vals = {"orchestrator": "kubernetes", "registry": "localhost:5000"}
        errs = canasta._validate_orchestrator_only(self.CMD, lambda n: vals.get(n))
        assert errs == []

    def test_no_orchestrator_selected_skips(self):
        vals = {"orchestrator": None, "registry": "localhost:5000"}
        errs = canasta._validate_orchestrator_only(self.CMD, lambda n: vals.get(n))
        assert errs == []

    def test_default_value_skips(self):
        cmd = {"parameters": [
            {"name": "registry", "orchestrator_only": "kubernetes",
             "default": "none"}]}
        vals = {"orchestrator": "compose", "registry": "none"}
        errs = canasta._validate_orchestrator_only(cmd, lambda n: vals.get(n))
        assert errs == []


class TestNamedValidators:
    CMD = {"parameters": [{"name": "domain_name", "validator": "hostname"}]}

    def test_valid_hostname_passes(self):
        vals = {"domain_name": "example.com"}
        errs = canasta._validate_named_validators(self.CMD, lambda n: vals.get(n))
        assert errs == []

    def test_invalid_hostname_fails_with_hint(self):
        vals = {"domain_name": "example.com:8443"}
        errs = canasta._validate_named_validators(self.CMD, lambda n: vals.get(n))
        assert len(errs) == 1
        code, msg = errs[0]
        assert code == 1
        assert "is not a valid hostname" in msg
        assert "HTTP_PORT/HTTPS_PORT" in msg

    def test_empty_value_skips(self):
        vals = {"domain_name": ""}
        errs = canasta._validate_named_validators(self.CMD, lambda n: vals.get(n))
        assert errs == []

    def test_unknown_validator_is_internal_error(self):
        cmd = {"parameters": [{"name": "x", "validator": "nope"}]}
        vals = {"x": "whatever"}
        errs = canasta._validate_named_validators(cmd, lambda n: vals.get(n))
        assert errs == [
            (2, "Internal error: parameter 'x' references unknown "
                "validator 'nope'")]


class TestCollectOrderAndExitCodes:
    def test_required_unless_reported_before_validator(self):
        # required_unless is evaluated before the named validators, so when
        # an unrelated param is missing its partner AND another param has a
        # bad value, the missing-partner error is first — that's what
        # main() exits on. (A single param can't trigger both: required_unless
        # fires only on an empty value, which the validators skip.)
        cmd = {"parameters": [
            {"name": "domain_name", "validator": "hostname"},
            {"name": "repo", "required_unless": "envfile"},
            {"name": "envfile"},
        ]}
        args = _args(domain_name="bad:8443", repo=None, envfile=None,
                     orchestrator=None)
        errs = canasta.collect_cli_param_errors(cmd, args)
        assert errs[0] == (
            1, "Error: --repo is required unless --envfile is provided")
        # the validator error is still collected, just after it
        assert any("is not a valid hostname" in m for _, m in errs)

    def test_all_valid_returns_empty(self):
        cmd = {"parameters": [{"name": "domain_name", "validator": "hostname"}]}
        args = _args(domain_name="example.com", orchestrator=None)
        assert canasta.collect_cli_param_errors(cmd, args) == []


class TestRequiredUnlessParity:
    """Both validation layers must be driven by the same metadata field.

    canasta.py keys off param['required_unless']; validate_params.yml's
    'Check required parameters' task keys off `item.required_unless`. If
    either side renamed the field the two layers would silently diverge.
    This asserts the Ansible task still references the same field name the
    CLI helper reads.
    """

    def test_ansible_layer_uses_required_unless_field(self):
        path = os.path.join(
            REPO_ROOT, "roles", "common", "tasks", "validate_params.yml")
        with open(path) as f:
            tasks = yaml.safe_load(f)
        required_task = next(
            t for t in tasks if t.get("name") == "Check required parameters")
        rendered = yaml.safe_dump(required_task)
        assert "required_unless" in rendered
