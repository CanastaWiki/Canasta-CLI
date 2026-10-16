"""Every git network operation and Gerrit request names the CLI.

Wikimedia's edge refuses generic user agents, including git's default, so
the play sets GIT_HTTP_USER_AGENT for every task, on the controller and on
targets, and the extension resolver sends the same agent.
"""

import os

import yaml
from jinja2 import Environment

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))


def _play():
    with open(os.path.join(REPO_ROOT, "canasta.yml")) as f:
        return yaml.safe_load(f)[0]


def test_agent_names_the_cli_version_and_repository():
    raw = _play()["vars"]["canasta_user_agent"]
    with open(os.path.join(REPO_ROOT, "VERSION")) as f:
        version = f.read().strip()
    rendered = Environment().from_string(
        raw.replace("lookup('file', playbook_dir + '/VERSION') | trim",
                    "version")).render(version=version)
    assert rendered == ("Canasta-CLI/%s (https://github.com/CanastaWiki/"
                        "Canasta-CLI)" % version)


def test_every_task_gets_the_git_agent():
    env = _play()["environment"]
    assert env["GIT_HTTP_USER_AGENT"] == "{{ canasta_user_agent }}"


def test_the_resolver_gets_the_agent():
    with open(os.path.join(REPO_ROOT, "roles", "extensions_skins", "tasks",
                           "add.yml")) as f:
        tasks = yaml.safe_load(f)
    resolve = next(t for t in tasks if "canasta_extension_resolve" in t)
    assert resolve["canasta_extension_resolve"]["user_agent"] == \
        "{{ canasta_user_agent | default(omit) }}"
