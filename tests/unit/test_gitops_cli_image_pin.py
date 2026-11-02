"""A CLI's own image pin is never stored in the shared env.template.

A version-shaped tag under the official repository follows the CLI that
manages the host, so each host's .env gets its own CLI's default image and
no host writes its version into the file every host shares. An image the
operator chose stays a shared literal.
"""

import base64
import os
import re
import sys

import jinja2
import yaml

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
sys.path.insert(0, os.path.join(REPO_ROOT, "filter_plugins"))
sys.path.insert(0, os.path.join(REPO_ROOT, "roles", "common", "module_utils"))
from canasta_gitops import (  # noqa: E402
    FilterModule,
    canasta_env_render as render,
    canasta_env_sync_plan as plan,
)
from canasta_image_pin import (  # noqa: E402
    CLI_IMAGE_PIN_PATTERN,
    is_cli_image_pin,
)

HOST_DEFAULT = "ghcr.io/canastawiki/canasta:3.5.30"
OTHER_CLI = "ghcr.io/canastawiki/canasta:3.5.29"
OPERATOR_IMAGES = (
    "ghcr.io/canastawiki/canasta:latest",
    "ghcr.io/canastawiki/canasta:1.43.9-20260801-228",
    "ghcr.io/example/canasta:3.5.29",
    "canasta:local",
)

BASE = "MW_SITE_SERVER={{mw_site_server}}\nHTTP_PORT=80\n"
VARS = {"mw_site_server": "https://example.com"}


def _image(rendered):
    lines = [line for line in rendered.splitlines()
             if line.startswith("CANASTA_IMAGE=")]
    assert len(lines) <= 1, rendered
    return lines[0].split("=", 1)[1] if lines else None


def _load(*parts):
    with open(os.path.join(REPO_ROOT, *parts)) as f:
        return yaml.safe_load(f)


class TestPattern:
    def test_matches_the_upgrade_rule(self):
        for task in _load("roles", "orchestrator", "tasks",
                          "upgrade_image_tag.yml"):
            facts = task.get("ansible.builtin.set_fact") or {}
            if "_upgrade_cli_pin_pattern" in facts:
                assert facts["_upgrade_cli_pin_pattern"] == CLI_IMAGE_PIN_PATTERN
                return
        raise AssertionError("_upgrade_cli_pin_pattern not set")

    def test_cli_pins(self):
        assert is_cli_image_pin(OTHER_CLI)
        assert is_cli_image_pin('"%s"' % OTHER_CLI)

    def test_operator_images(self):
        for image in OPERATOR_IMAGES + ("", None):
            assert not is_cli_image_pin(image), image

    def test_is_a_filter(self):
        assert FilterModule().filters()["canasta_cli_image_pin"] is is_cli_image_pin


class TestRender:
    def test_no_image_in_template_gets_the_host_default(self):
        out = render(BASE, VARS, HOST_DEFAULT)
        assert _image(out) == HOST_DEFAULT
        assert "HTTP_PORT=80\nCANASTA_IMAGE=%s\n" % HOST_DEFAULT in out

    def test_a_cli_pin_in_template_gets_the_host_default(self):
        template = "CANASTA_IMAGE=%s\n%s" % (OTHER_CLI, BASE)
        out = render(template, VARS, HOST_DEFAULT)
        assert out.startswith("CANASTA_IMAGE=%s\n" % HOST_DEFAULT)
        assert _image(out) == HOST_DEFAULT

    def test_an_operator_image_is_kept(self):
        for image in OPERATOR_IMAGES:
            template = "CANASTA_IMAGE=%s\n%s" % (image, BASE)
            assert _image(render(template, VARS, HOST_DEFAULT)) == image

    def test_without_a_default_the_template_is_copied(self):
        template = "CANASTA_IMAGE=%s\n%s" % (OTHER_CLI, BASE)
        assert _image(render(template, VARS)) == OTHER_CLI
        assert _image(render(BASE, VARS)) is None

    def test_the_shared_render_passes_the_host_default(self):
        task = _load("roles", "gitops", "tasks", "_render_env.yml")[-1]
        content = task["ansible.builtin.copy"]["content"]
        assert "canasta_default_image" in content


class TestSync:
    def _live(self, image):
        return render(BASE, VARS) + ("CANASTA_IMAGE=%s\n" % image if image else "")

    def test_a_cli_pin_in_env_is_never_written(self):
        for template in (BASE, "CANASTA_IMAGE=%s\n%s" % (OTHER_CLI, BASE)):
            p = plan(self._live(HOST_DEFAULT), template, VARS, template, VARS)
            assert p == {"set": {}, "unset": [], "conflict": []}, template

    def test_a_cli_pin_in_env_without_a_baseline_is_never_written(self):
        p = plan(self._live(HOST_DEFAULT), BASE, VARS)
        assert p == {"set": {}, "unset": [], "conflict": []}

    def test_an_operator_image_set_in_env_is_written(self):
        for template in (BASE, "CANASTA_IMAGE=%s\n%s" % (OTHER_CLI, BASE)):
            p = plan(self._live("ghcr.io/canastawiki/canasta:latest"),
                     template, VARS, template, VARS)
            assert p["set"] == {
                "CANASTA_IMAGE": "ghcr.io/canastawiki/canasta:latest"}

    def test_an_operator_image_pulled_into_the_template_is_left_for_render(self):
        before = BASE
        after = "CANASTA_IMAGE=ghcr.io/canastawiki/canasta:latest\n" + BASE
        p = plan(self._live(HOST_DEFAULT), after, VARS, before, VARS)
        assert p == {"set": {}, "unset": [], "conflict": []}

    def test_moving_env_back_to_the_cli_default_drops_the_operator_image(self):
        template = "CANASTA_IMAGE=ghcr.io/canastawiki/canasta:latest\n" + BASE
        p = plan(self._live(HOST_DEFAULT), template, VARS, template, VARS)
        assert p["unset"] == ["CANASTA_IMAGE"]


class TestInit:
    @staticmethod
    def _capture(env):
        task = next(t for t in _load("roles", "gitops", "tasks",
                                     "init_compose.yml")
                    if t.get("name") == "Generate env.template with placeholder syntax")
        jenv = jinja2.Environment(trim_blocks=True)
        jenv.filters.update(FilterModule().filters())
        jenv.filters["regex_search"] = lambda v, pattern: re.search(pattern, v)
        jenv.filters["b64decode"] = lambda v: base64.b64decode(v).decode()
        return jenv.from_string(task["ansible.builtin.copy"]["content"]).render(
            _gitops_env_raw={"content": base64.b64encode(env.encode()).decode()},
            canasta_secret_key_regex="PASSWORD$",
            canasta_host_specific_nonsecret=["MW_SITE_SERVER"],
            _gitops_secret_keys=[])

    def test_init_leaves_a_cli_pin_out_of_env_template(self):
        out = self._capture("CANASTA_IMAGE=%s\nHTTP_PORT=80\n" % OTHER_CLI)
        assert "CANASTA_IMAGE" not in out
        assert "HTTP_PORT=80" in out

    def test_init_keeps_an_operator_image(self):
        for image in OPERATOR_IMAGES:
            out = self._capture("CANASTA_IMAGE=%s\nMYSQL_PASSWORD=pw\n" % image)
            assert "CANASTA_IMAGE=%s\n" % image in out
            assert "MYSQL_PASSWORD={{mysql_password}}" in out


class TestConfigSet:
    def test_setting_a_cli_pin_drops_the_literal(self):
        tasks = _load("roles", "config", "tasks", "_update_gitops_var.yml")
        block = next(t for t in tasks if t.get("name")
                     == "Update env.template literal when key has no placeholder")
        li = block["block"][0]["ansible.builtin.lineinfile"]
        state = str(li["state"])
        assert "canasta_cli_image_pin" in state and "'absent'" in state
