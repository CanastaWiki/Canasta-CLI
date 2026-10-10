"""The K8s backup's database dump runs the image the web pods run.

For a --build-from or custom image the web pods run the in-cluster
registry's copy (10.43.0.2:5000/...), which CANASTA_IMAGE in .env does not
name, so taking the image from .env left the Job unable to pull it.
"""

import os

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
RUN_BACKUP = os.path.join(REPO_ROOT, "roles", "orchestrator", "tasks",
                          "k8s_run_backup.yml")


def _tasks():
    with open(RUN_BACKUP) as f:
        return yaml.safe_load(f)


def _dump_image_expr():
    task = next(t for t in _tasks()
                if t.get("name") == "Build init containers for backup ops")
    dump = next(c for c in task["ansible.builtin.set_fact"][
        "_backup_init_containers"] if c["name"] == "dump-databases")
    return dump["image"]


def _image(web_stdout, env_image=None):
    env = {"CANASTA_IMAGE": env_image} if env_image else {}
    templar = Templar(loader=DataLoader(), variables={
        "_backup_web_image": {"stdout": web_stdout},
        "_backup_env": {"variables": env}})
    return str(templar.template(trust_as_template(_dump_image_expr()))).strip()


def test_uses_the_web_pods_image():
    assert _image("10.43.0.2:5000/canasta:local\n", "canasta:local") == (
        "10.43.0.2:5000/canasta:local")


def test_falls_back_to_canasta_image_then_the_default():
    assert _image("", "ghcr.io/canastawiki/canasta:3.5.26") == (
        "ghcr.io/canastawiki/canasta:3.5.26")
    assert _image("") == "ghcr.io/canastawiki/canasta:latest"


def test_the_web_image_is_read_before_the_job_is_built():
    names = [t.get("name") for t in _tasks()]
    read = names.index("Read the image the web pods run")
    assert read < names.index("Build init containers for backup ops")
    cmd = _tasks()[read]["ansible.builtin.command"]["argv"]
    assert 'jsonpath={.spec.template.spec.containers[?(@.name=="web")].image}' in cmd
