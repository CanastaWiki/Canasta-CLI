"""Converting a loose clone to a submodule keeps the commit it was on.

`git submodule add` clones the remote and stages a gitlink at the remote's
HEAD. _convert_submodule.yml checks the original commit back out, and must
stage it too: otherwise the gitlink records the remote's newer commit, and
the next `git submodule update` moves the working tree to it.
"""

import os
import shutil
import subprocess

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
TASKS = os.path.join(
    REPO_ROOT, "roles", "gitops", "tasks", "_convert_submodule.yml")

GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
    "GIT_CONFIG_COUNT": "1",
    "GIT_CONFIG_KEY_0": "protocol.file.allow",
    "GIT_CONFIG_VALUE_0": "always",
}


def git(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=cwd, env=GIT_ENV, check=True,
        capture_output=True, text=True).stdout.strip()


def _block():
    with open(TASKS) as f:
        tasks = yaml.safe_load(f)
    return next(t for t in tasks if t.get("name") == "Convert to submodule")


def _convert(instance, repo_path, url):
    """Run the conversion block's steps against real repositories."""
    variables = {"_repo_path": str(repo_path), "instance_path": str(instance),
                 "_sub_url": {"rc": 0, "stdout": url}}
    for task in _block()["block"]:
        templar = Templar(loader=DataLoader(), variables=variables)
        if "ansible.builtin.set_fact" in task:
            for key, value in task["ansible.builtin.set_fact"].items():
                variables[key] = templar.template(trust_as_template(value))
        elif "ansible.builtin.file" in task:
            shutil.rmtree(templar.template(
                trust_as_template(task["ansible.builtin.file"]["path"])))
        else:
            cmd = task["ansible.builtin.command"]
            result = subprocess.run(
                templar.template(trust_as_template(cmd["cmd"])), shell=True,
                cwd=templar.template(trust_as_template(cmd["chdir"])),
                env=GIT_ENV, capture_output=True, text=True)
            if task.get("failed_when") is not False:
                assert result.returncode == 0, (task["name"], result.stderr)
            if task.get("register"):
                variables[task["register"]] = {
                    "rc": result.returncode, "stdout": result.stdout}


def test_gitlink_records_the_commit_that_was_checked_out(tmp_path):
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    git(upstream, "init", "-q", "-b", "main")
    (upstream / "extension.json").write_text("{}\n")
    git(upstream, "add", ".")
    git(upstream, "commit", "-q", "-m", "v1")
    deployed = git(upstream, "rev-parse", "HEAD")

    instance = tmp_path / "instance"
    instance.mkdir()
    git(instance, "init", "-q", "-b", "main")
    clone = instance / "extensions" / "Ext"
    git(tmp_path, "clone", "-q", upstream.as_uri(), str(clone))

    # Upstream moves on after the extension was deployed.
    (upstream / "extension.json").write_text("{\"v\": 2}\n")
    git(upstream, "commit", "-q", "-am", "v2")
    newer = git(upstream, "rev-parse", "HEAD")
    assert newer != deployed

    _convert(instance, clone, upstream.as_uri())

    staged = git(instance, "ls-files", "--stage", "extensions/Ext").split()[1]
    assert staged == deployed, "gitlink records the remote's newer HEAD"
    git(instance, "submodule", "update", "--init", "--recursive")
    assert git(clone, "rev-parse", "HEAD") == deployed
