"""backfill_mycnf_template.yml, run end to end on a local git checkout.

Each case runs the migration as a playbook on localhost and checks the
repository it leaves: which files exist, what is tracked and what is staged.
"""

import os
import subprocess
import sys

import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
BACKFILL = os.path.join(REPO_ROOT, "roles", "gitops", "tasks",
                        "backfill_mycnf_template.yml")
POOL_CNF = "[mysqld]\ninnodb_buffer_pool_size = 4G\n"
LOCKED = b"\x00GITCRYPT\x00rest"


def _git(inst, *args):
    return subprocess.run(["git", *args], cwd=inst, check=True,
                          capture_output=True, text=True).stdout


def _instance(tmp_path, *, gitops=True, mycnf=POOL_CNF, host_vars="a: 1\n",
              shared_vars=None, template=None, tracked=True, locked=False):
    inst = tmp_path / "inst"
    (inst / "hosts" / "h1").mkdir(parents=True)
    _git(inst, "init", "-q", "-b", "main")
    _git(inst, "config", "user.email", "t@example.com")
    _git(inst, "config", "user.name", "t")
    (inst / "hosts" / "hosts.yaml").write_bytes(
        LOCKED if locked else b"hosts:\n  - name: h1\n")
    if gitops:
        (inst / ".gitops-host").write_text("h1\n")
    if mycnf is not None:
        (inst / "my.cnf").write_text(mycnf)
    if host_vars is not None:
        (inst / "hosts" / "h1" / "vars.yaml").write_text(host_vars)
    if shared_vars is not None:
        (inst / "hosts" / "_shared").mkdir()
        (inst / "hosts" / "_shared" / "vars.yaml").write_text(shared_vars)
    if template is not None:
        (inst / "my.cnf.template").write_text(template)
    _git(inst, "add", "-A")
    if not tracked:
        _git(inst, "rm", "-q", "--cached", "--ignore-unmatch", "--", "my.cnf")
    _git(inst, "commit", "-q", "-m", "init", "--allow-empty")
    return inst


def _run(tmp_path):
    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {"instance_path": str(tmp_path / "inst"),
                 "canasta_root": REPO_ROOT,
                 "ansible_python_interpreter": sys.executable},
        "tasks": [{"ansible.builtin.include_tasks": BACKFILL}],
    }]))
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               ANSIBLE_LOCAL_TEMP=str(tmp_path / "ansible-tmp"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env, cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc.stdout + proc.stderr


def _staged(inst):
    return set(_git(inst, "diff", "--cached", "--name-status").splitlines())


def _shared(inst):
    path = inst / "hosts" / "_shared" / "vars.yaml"
    return yaml.safe_load(path.read_text()) if path.exists() else None


def test_migrates_a_tracked_my_cnf(tmp_path):
    inst = _instance(tmp_path)
    out = _run(tmp_path)
    assert (inst / "my.cnf.template").read_text() == (
        "[mysqld]\ninnodb_buffer_pool_size = {{innodb_buffer_pool_size}}\n")
    assert _shared(inst) == {"innodb_buffer_pool_size": "4G"}
    assert _staged(inst) == {"D\tmy.cnf", "A\tmy.cnf.template",
                             "A\thosts/_shared/vars.yaml"}
    assert (inst / "my.cnf").exists()
    assert "my.cnf is now a rendered, per-host file" in out


def test_keeps_existing_shared_vars(tmp_path):
    inst = _instance(tmp_path, shared_vars="aws_key: k\n")
    _run(tmp_path)
    assert _shared(inst) == {"aws_key": "k", "innodb_buffer_pool_size": "4G"}


def test_a_value_already_recorded_is_left_alone(tmp_path):
    inst = _instance(tmp_path, host_vars="innodb_buffer_pool_size: 1G\n",
                     shared_vars="aws_key: k\n")
    _run(tmp_path)
    assert _shared(inst) == {"aws_key": "k"}
    assert "D\tmy.cnf" in _staged(inst)
    assert (inst / "my.cnf.template").exists()


def test_not_a_gitops_instance(tmp_path):
    inst = _instance(tmp_path, gitops=False)
    _run(tmp_path)
    assert not (inst / "my.cnf.template").exists()
    assert _staged(inst) == set()


def test_no_buffer_pool_means_nothing_to_migrate(tmp_path):
    inst = _instance(tmp_path, mycnf="[client]\n")
    out = _run(tmp_path)
    assert not (inst / "my.cnf.template").exists()
    assert _staged(inst) == set()
    assert "sets no innodb_buffer_pool_size" in out


def test_locked_checkout_is_skipped_with_a_warning(tmp_path):
    inst = _instance(tmp_path, locked=True, host_vars="\x00binary\n")
    out = _run(tmp_path)
    assert not (inst / "my.cnf.template").exists()
    assert _staged(inst) == set()
    assert "git-crypt is locked" in out


def test_an_already_migrated_instance_is_untouched(tmp_path):
    # No host vars: reading them would fail, so this also shows they are
    # not read once the migration is done.
    inst = _instance(tmp_path, template="custom\n", tracked=False,
                     host_vars=None)
    _run(tmp_path)
    assert (inst / "my.cnf.template").read_text() == "custom\n"
    assert _staged(inst) == set()


def test_an_interrupted_migration_converges(tmp_path):
    inst = _instance(tmp_path, template="custom\n")
    _run(tmp_path)
    assert (inst / "my.cnf.template").read_text() == "custom\n"
    assert _shared(inst) == {"innodb_buffer_pool_size": "4G"}
    assert "D\tmy.cnf" in _staged(inst)
