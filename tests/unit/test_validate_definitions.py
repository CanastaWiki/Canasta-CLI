"""Tests for the validate_definitions.py script."""

import os
import sys

import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
import validate_definitions


class TestValidateMain:
    """Test the actual main() function against the real repo."""

    def test_real_definitions_pass(self):
        """The real command definitions should pass validation."""
        try:
            validate_definitions.main()
        except SystemExit as e:
            assert e.code == 0 or e.code is None, (
                "Validation failed on real definitions"
            )


class TestExampleChecks:
    """A documented example has to be an invocation the CLI accepts.

    The examples feed the generated command reference, so one that
    argparse rejects ships as published documentation. The case that
    got through: `install` gained a `podman` example and a `podman`
    mention in three descriptions, but not in the `choices:` list that
    is actually enforced.
    """

    INSTALL = {
        "name": "install",
        "description": "Install dependencies",
        "playbook": "install.yml",
        "parameters": [
            {"name": "host", "type": "string", "short": "H",
             "description": "Target host"},
            {"name": "verbose_output", "type": "bool", "long": "loud",
             "description": "Chatter"},
            {"name": "packages", "type": "string", "description": "Packages",
             "positional": True, "multi": True,
             "choices": ["docker", "sops", "canasta"]},
        ],
    }
    CREATE = {
        "name": "create",
        "description": "Create an instance",
        "playbook": "create.yml",
        "parameters": [
            {"name": "id", "type": "string", "short": "i", "description": "ID"},
            {"name": "orchestrator", "type": "choice",
             "choices": ["compose", "k8s"], "description": "Orchestrator"},
        ],
    }

    def _defs(self):
        return {"commands": [self.INSTALL, self.CREATE], "command_groups": [],
                "global_flags": [{"name": "verbose", "short": "v",
                                  "type": "bool", "description": "Verbose"}]}

    def _check(self, command, example):
        import cli_examples
        data = self._defs()
        table = cli_examples.build_command_table(data)
        return validate_definitions.check_example(command, example, table, data)

    def test_positional_outside_choices_is_rejected(self):
        errors = self._check(self.INSTALL, "canasta install podman")
        assert errors and "'podman' is not one of" in errors[0]

    def test_positional_in_choices_passes(self):
        assert self._check(self.INSTALL, "canasta install docker") == []

    def test_every_value_of_a_multi_positional_is_checked(self):
        errors = self._check(self.INSTALL, "canasta install docker podman")
        assert len(errors) == 1 and "'podman'" in errors[0]

    def test_choice_flag_outside_choices_is_rejected(self):
        errors = self._check(self.CREATE, "canasta create -i x --orchestrator nomad")
        assert errors and "--orchestrator: 'nomad' is not one of" in errors[0]

    def test_choice_flag_accepts_inline_value_form(self):
        assert self._check(self.CREATE, "canasta create --orchestrator=k8s") == []

    def test_flag_value_is_not_mistaken_for_a_positional(self):
        # -H takes a value; 'prod1' must not be checked against packages.
        assert self._check(self.INSTALL, "canasta install -H prod1 docker") == []

    def test_bool_flag_does_not_consume_the_next_token(self):
        errors = self._check(self.INSTALL, "canasta install --loud podman")
        assert errors and "'podman' is not one of" in errors[0]

    def test_global_flags_are_accepted(self):
        assert self._check(self.INSTALL, "canasta install -v docker") == []

    def test_unknown_flag_is_reported(self):
        assert self._check(self.INSTALL, "canasta install --nope docker") == [
            "unknown flag: --nope"
        ]

    def test_unknown_command_is_reported(self):
        errors = self._check(self.INSTALL, "canasta instal docker")
        assert errors and errors[0].startswith("unknown command:")

    def test_example_listed_under_the_wrong_command_is_reported(self):
        assert self._check(self.INSTALL, "canasta create -i x") == [
            "invokes 'create'"
        ]

    def test_placeholder_values_are_not_checked(self):
        # <package> is the author deferring to the reader, not a value.
        assert self._check(self.INSTALL, "canasta install <package>") == []

    def test_compound_line_is_unwrapped_before_checking(self):
        errors = self._check(self.INSTALL, "cd /tmp && canasta install podman")
        assert errors and "'podman' is not one of" in errors[0]

    def test_real_examples_all_parse(self):
        """Every example in the real definitions is a valid invocation."""
        import cli_examples
        repo_root = os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))
        ))
        with open(os.path.join(repo_root, "meta",
                               "command_definitions.yml")) as f:
            data = yaml.safe_load(f)
        table = cli_examples.build_command_table(data)
        offenders = []
        for cmd in data["commands"]:
            for example in cmd.get("examples") or []:
                for problem in validate_definitions.check_example(
                    cmd, example, table, data
                ):
                    offenders.append("%s: %s -- %s"
                                     % (cmd["name"], example, problem))
        assert not offenders, "\n  ".join([""] + offenders)


class TestDispatchChecks:
    """A command's accepted values and the branches its playbook takes
    have to be the same set.

    Both directions are bugs and neither is visible at runtime: a choice
    nothing branches on exits 0 having done nothing, and a branch no
    choice can reach is dead code that looks live — which is how
    `when: "'podman' in _install_packages"` sat in install.yml while
    `choices:` still rejected `podman`.
    """

    REPO_ROOT = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..")
    )

    def _playbook(self, name):
        return os.path.join(self.REPO_ROOT, "playbooks", name)

    def _write(self, tmpdir, body):
        path = os.path.join(tmpdir, "pb.yml")
        with open(path, "w") as f:
            f.write(body)
        return path

    IDIOM = """---
- name: Split the requested packages
  ansible.builtin.set_fact:
    _pkgs: "{{ packages.split() }}"

- name: Install one
  ansible.builtin.debug:
    msg: one
  when: "'one' in _pkgs"

- name: Install two
  ansible.builtin.debug:
    msg: two
  when:
    - "'two' in _pkgs"
    - some_other_condition
"""

    def test_the_fact_and_its_branches_are_found(self, tmp_dir):
        path = self._write(tmp_dir, self.IDIOM)
        fact, literals = validate_definitions.dispatch_literals(path, "packages")
        assert fact == "_pkgs"
        assert literals == {"one", "two"}

    def test_a_branch_with_no_matching_choice_is_reported(self, tmp_dir):
        path = self._write(tmp_dir, self.IDIOM)
        param = {"name": "packages", "choices": ["one"]}
        assert validate_definitions.check_dispatch({}, param, path) == [
            "_pkgs branches on 'two', which is not an accepted choice"
        ]

    def test_a_choice_nothing_branches_on_is_reported(self, tmp_dir):
        path = self._write(tmp_dir, self.IDIOM)
        param = {"name": "packages", "choices": ["one", "two", "three"]}
        assert validate_definitions.check_dispatch({}, param, path) == [
            "'three' is an accepted choice but nothing dispatches on it"
        ]

    def test_agreement_is_silent(self, tmp_dir):
        path = self._write(tmp_dir, self.IDIOM)
        param = {"name": "packages", "choices": ["one", "two"]}
        assert validate_definitions.check_dispatch({}, param, path) == []

    def test_a_playbook_without_the_idiom_is_not_checked(self, tmp_dir):
        # create --orchestrator and gitops init --role consume their
        # value rather than branching on it; demanding a branch per
        # choice would be a false positive.
        path = self._write(tmp_dir, "---\n- name: Noop\n  ansible.builtin.debug:\n"
                                    "    msg: hi\n")
        param = {"name": "packages", "choices": ["one"]}
        assert validate_definitions.dispatch_literals(path, "packages") == (
            None, set())
        assert validate_definitions.check_dispatch({}, param, path) == []

    def test_an_unrelated_membership_test_is_not_a_dispatch(self, tmp_dir):
        # _purge_host.yml has `'Deleted' in _purge_crictl.stdout`, which
        # is a string test on command output, not a package branch.
        path = self._write(tmp_dir, self.IDIOM + """
- name: Unrelated
  ansible.builtin.debug:
    msg: x
  changed_when: "'Deleted' in _other.stdout"
  when: "'Deleted' in _other.stdout"
""")
        _, literals = validate_definitions.dispatch_literals(path, "packages")
        assert literals == {"one", "two"}

    def test_install_choices_and_branches_agree(self):
        param = {"name": "packages",
                 "choices": ["docker", "k8s-cp", "k8s-worker", "git-crypt",
                             "sops", "podman", "canasta", "uv"]}
        assert validate_definitions.check_dispatch(
            {}, param, self._playbook("install.yml")) == []

    def test_uninstall_choices_and_branches_agree(self):
        param = {"name": "packages", "choices": ["k8s"]}
        assert validate_definitions.check_dispatch(
            {}, param, self._playbook("uninstall.yml")) == []


class TestDirectOnlyInvariants:
    """Invariants that have to hold for 'direct_only: true' commands.

    These catch two failure modes that validate_definitions.py can't
    see on its own:

    1. A command is declared 'direct_only: true' in
       command_definitions.yml but has no matching handler registered
       in direct_commands.py. At runtime the command would have no
       code path at all — no handler, no playbook.
    2. A command has 'direct_only: true' AND a 'playbook:' field (the
       XOR already caught by validate_definitions, re-asserted here
       at the data layer so it's discoverable in this test file too).
    """

    def _real_commands(self):
        script_dir = os.path.dirname(
            os.path.abspath(validate_definitions.__file__)
        )
        defn_path = os.path.join(
            os.path.dirname(script_dir),
            "meta", "command_definitions.yml",
        )
        with open(defn_path) as f:
            return yaml.safe_load(f).get("commands", [])

    def _direct_commands_module(self):
        # direct_commands imports yaml at module scope; add repo root
        # to sys.path so the import resolves in CI environments that
        # don't ship the repo as a package.
        script_dir = os.path.dirname(
            os.path.abspath(validate_definitions.__file__)
        )
        repo_root = os.path.dirname(script_dir)
        if repo_root not in sys.path:
            sys.path.insert(0, repo_root)
        import direct_commands
        return direct_commands

    def test_every_direct_only_command_has_a_handler(self):
        dc = self._direct_commands_module()
        missing = []
        for cmd in self._real_commands():
            if cmd.get("direct_only"):
                name = cmd.get("name", "?")
                if not dc.is_direct_command(name):
                    missing.append(name)
        assert not missing, (
            "direct_only commands with no direct_commands.py handler: %s"
            % ", ".join(missing)
        )

    def test_no_command_declares_both_direct_only_and_playbook(self):
        conflicts = []
        for cmd in self._real_commands():
            if cmd.get("direct_only") and cmd.get("playbook"):
                conflicts.append(cmd.get("name", "?"))
        assert not conflicts, (
            "Commands with both direct_only AND playbook: %s"
            % ", ".join(conflicts)
        )


class TestValidateStructure:
    def _make_defs(self, tmpdir, commands, playbooks=None):
        defn = {"commands": commands}
        defn_path = os.path.join(tmpdir, "meta", "command_definitions.yml")
        os.makedirs(os.path.dirname(defn_path), exist_ok=True)
        with open(defn_path, "w") as f:
            yaml.dump(defn, f)

        pb_dir = os.path.join(tmpdir, "playbooks")
        os.makedirs(pb_dir, exist_ok=True)
        for pb in (playbooks or []):
            with open(os.path.join(pb_dir, pb), "w") as f:
                f.write("---\n")
        return tmpdir

    def test_valid_structure(self, tmp_dir):
        self._make_defs(tmp_dir, [
            {"name": "test", "description": "Test", "playbook": "test.yml",
             "parameters": [{"name": "id", "type": "string", "description": "ID"}]},
        ], ["test.yml"])
        defn_path = os.path.join(tmp_dir, "meta", "command_definitions.yml")
        with open(defn_path) as f:
            data = yaml.safe_load(f)
        errors = []
        for cmd in data["commands"]:
            for field in validate_definitions.REQUIRED_CMD_FIELDS:
                if field not in cmd:
                    errors.append("missing %s" % field)
        assert len(errors) == 0

    def test_missing_field_detected(self):
        cmd = {"name": "test", "playbook": "test.yml", "parameters": []}
        missing = [f for f in validate_definitions.REQUIRED_CMD_FIELDS if f not in cmd]
        assert "description" in missing

    def test_invalid_type_detected(self):
        assert "invalid" not in validate_definitions.VALID_TYPES

    def test_valid_types(self):
        for t in ["string", "path", "bool", "choice", "integer"]:
            assert t in validate_definitions.VALID_TYPES

    def test_duplicate_names_detected(self):
        names = ["create", "delete", "create"]
        seen = set()
        dupes = [n for n in names if n in seen or seen.add(n)]
        assert "create" in dupes

    def test_underscore_prefix_skipped(self, tmp_dir):
        self._make_defs(tmp_dir, [], ["_helper.yml"])
        pb_dir = os.path.join(tmp_dir, "playbooks")
        files = [f for f in os.listdir(pb_dir) if f.endswith(".yml") and not f.startswith("_")]
        assert "_helper.yml" not in files


class TestDescriptionLint:
    """Guard against redundant parentheticals in parameter descriptions
    that duplicate what the wiki flag table already conveys:

    - '(optional)' is redundant with an unchecked Required column.
    - '(required)' is redundant with a checked Required column.
    - '(default: X)' is redundant when the parameter has a literal
      `default: X` field (the wiki table's Default column shows it).
    """

    REPO_ROOT = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..")
    )
    DEFS_PATH = os.path.join(REPO_ROOT, "meta", "command_definitions.yml")

    def _all_params(self):
        with open(self.DEFS_PATH) as f:
            data = yaml.safe_load(f)
        params = [("<global>", p) for p in data.get("global_flags", [])]
        for c in data.get("commands", []):
            for p in c.get("parameters", []) or []:
                params.append((c["name"], p))
        return params

    def test_no_optional_markers(self):
        offenders = []
        for cmd, p in self._all_params():
            if "(optional)" in p.get("description", "").lower():
                offenders.append("%s.%s" % (cmd, p["name"]))
        assert not offenders, (
            "descriptions contain redundant '(optional)' — remove it "
            "(the Required column already conveys this):\n  "
            + "\n  ".join(offenders)
        )

    def test_no_required_markers(self):
        offenders = []
        for cmd, p in self._all_params():
            if "(required)" in p.get("description", "").lower():
                offenders.append("%s.%s" % (cmd, p["name"]))
        assert not offenders, (
            "descriptions contain redundant '(required)' — remove it "
            "(the Required column already conveys this):\n  "
            + "\n  ".join(offenders)
        )

    def test_no_redundant_default_markers(self):
        import re
        pat = re.compile(r"\(default:\s*[^)]+\)", re.IGNORECASE)
        offenders = []
        for cmd, p in self._all_params():
            # Only flag when the YAML also has a literal default — a
            # parenthetical 'default: localhost' describing runtime
            # behavior on a param with no YAML default is meaningful.
            if "default" in p and pat.search(p.get("description", "")):
                offenders.append("%s.%s" % (cmd, p["name"]))
        assert not offenders, (
            "descriptions contain '(default: ...)' that duplicates "
            "the YAML `default:` field — remove from the description:\n  "
            + "\n  ".join(offenders)
        )


class TestAutoConfirmInvariant:
    """canasta.py auto-prompts 'Continue? [y/N]' for any command whose
    parameters include one named 'yes' (the destructive-confirmation net).
    A command that is read-only unless --yes is given (so prompting before
    it has done anything is wrong) must opt out with 'self_confirm: true'
    and gate writes itself — e.g. 'config refresh-template' with no args
    lists drift and must not prompt first.
    """

    def _real_commands(self):
        repo_root = os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))
        ))
        defn_path = os.path.join(
            repo_root, "meta", "command_definitions.yml",
        )
        with open(defn_path) as f:
            return yaml.safe_load(f).get("commands", [])

    def _canasta(self):
        repo_root = os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))
        ))
        if repo_root not in sys.path:
            sys.path.insert(0, repo_root)
        import canasta
        return canasta

    def test_refresh_template_opts_out_of_auto_confirm(self):
        cmd = next(
            c for c in self._real_commands()
            if c["name"] == "config_refresh_template"
        )
        assert cmd.get("self_confirm") is True, (
            "config_refresh_template defines a 'yes' parameter, so it must "
            "set 'self_confirm: true' or canasta.py's auto-confirm prompt "
            "fires on its read-only no-arg/preview modes."
        )

    def test_self_confirm_suppresses_the_prompt(self):
        canasta = self._canasta()
        yes_cmd = {"parameters": [{"name": "yes"}]}
        # A plain destructive command (e.g. delete) still prompts.
        assert canasta.should_prompt_confirmation(yes_cmd, False) is True
        assert canasta.should_prompt_confirmation(yes_cmd, True) is False
        # self_confirm opts out entirely.
        optout = dict(yes_cmd, self_confirm=True)
        assert canasta.should_prompt_confirmation(optout, False) is False
        # No 'yes' parameter -> never prompts.
        assert canasta.should_prompt_confirmation({"parameters": []}, False) \
            is False
