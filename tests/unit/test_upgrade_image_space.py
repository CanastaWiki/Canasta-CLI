"""upgrade refuses an instance whose image pull would not fit on the disk.

A pull that runs out of space fails while unpacking, after the upgrade has
refreshed the instance's configuration. The check runs first, against the
filesystem the runtime stores images on, and leaves the instance as it is.

Runs the real check_image_space.yml against a stubbed runtime and `df`.
"""

import os
import stat
import subprocess
import sys

import pytest
import yaml

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CHECK = os.path.join(REPO_ROOT, "roles", "upgrade", "tasks", "check_image_space.yml")
SINGLE = os.path.join(REPO_ROOT, "playbooks", "_upgrade_single.yml")
COMMON = os.path.join(REPO_ROOT, "roles", "common")

CURRENT = "ghcr.io/canastawiki/canasta:3.5.28"
TARGET = "ghcr.io/canastawiki/canasta:3.5.29"
GIB = 1024 ** 3

# Answers `image inspect` for the images named in STUB_PRESENT, and reports
# the image store at STUB_STORE.
RUNTIME = r"""#!/bin/sh
case "$1 $2" in
  "image inspect")
    for ref in $STUB_PRESENT; do
      if [ "$ref" = "$3" ]; then echo "$STUB_SIZE"; exit 0; fi
    done
    exit 1 ;;
  "info --format")
    case "$3" in
      *DriverStatus*) echo "$STUB_DRIVER" ;;
      *) echo "$STUB_STORE" ;;
    esac ;;
  *) exit 2 ;;
esac
"""

DF = r"""#!/bin/sh
echo "$*" >> "$STUB_LOG"
case "$1" in
  -Pi)
    echo "Filesystem Inodes IUsed IFree IUse% Mounted"
    echo "/dev/sda1 $STUB_ITOTAL 0 $STUB_IFREE 50% /" ;;
  *)
    echo "Filesystem 1024-blocks Used Available Capacity Mounted"
    echo "/dev/sda1 100000000 0 $STUB_FREE_KB 50% /" ;;
esac
"""


def _stub(bin_dir, name, body):
    path = bin_dir / name
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _run(tmp_path, cli="docker", size=4 * GIB, free=2 * GIB,
         present=(CURRENT,), current=CURRENT, store=None,
         itotal=10240000, ifree=5000000, driver="", containerd_conf=None):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub(bin_dir, cli, RUNTIME)
    _stub(bin_dir, "df", DF)
    if store is None:
        store = tmp_path / "images"
        store.mkdir()
    site = tmp_path / "site"
    site.mkdir()
    (site / ".env").write_text("CANASTA_IMAGE=%s\n" % current)
    check = CHECK
    if containerd_conf is not None:
        conf = tmp_path / "config.toml"
        conf.write_text(containerd_conf)
        check = tmp_path / "check_image_space.yml"
        with open(CHECK) as f:
            check.write_text(f.read().replace(
                "/etc/containerd/config.toml", str(conf)))

    play = tmp_path / "play.yml"
    play.write_text(yaml.safe_dump([{
        "hosts": "localhost",
        "connection": "local",
        "gather_facts": False,
        "vars": {"canasta_root": REPO_ROOT, "instance_id": "site",
                 "instance_path": str(site), "inspect_command": cli,
                 "canasta_default_image": TARGET,
                 "ansible_python_interpreter": sys.executable},
        "tasks": [
            {"ansible.builtin.include_tasks": str(check)},
            {"ansible.builtin.debug": {
                "msg": "REASON=[{{ _upgrade_space_reason }}]"}},
        ],
    }]))
    log = tmp_path / "df.log"
    env = dict(os.environ, ANSIBLE_NOCOLOR="1", ANSIBLE_LOCALHOST_WARNING="0",
               PATH="%s:%s" % (bin_dir, os.environ["PATH"]),
               STUB_LOG=str(log), STUB_PRESENT=" ".join(present),
               STUB_SIZE=str(size), STUB_STORE=str(store),
               STUB_DRIVER=driver,
               STUB_FREE_KB=str(free // 1024),
               STUB_ITOTAL=str(itotal), STUB_IFREE=str(ifree),
               ANSIBLE_LIBRARY=os.path.join(COMMON, "library"),
               ANSIBLE_MODULE_UTILS=os.path.join(COMMON, "module_utils"))
    playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    proc = subprocess.run([playbook, "-i", "localhost,", str(play)],
                          capture_output=True, text=True, env=env, cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    reason = proc.stdout.split("REASON=[", 1)[1].split("]", 1)[0]
    df_calls = log.read_text().splitlines() if log.exists() else []
    return reason, df_calls, store


class TestTheCheck:
    @pytest.mark.parametrize("cli", ["docker", "podman"])
    def test_an_image_larger_than_the_free_space_is_refused(self, tmp_path, cli):
        reason, df_calls, store = _run(tmp_path, cli=cli)
        assert reason.startswith("'site': not upgraded, because its image "
                                 "needs up to 4.00 GB and 2.00 GB is free on ")
        assert "canasta image prune" in reason
        assert df_calls == ["-Pk %s" % store, "-Pi %s" % store]

    def test_too_few_free_inodes_is_refused(self, tmp_path):
        reason, _, store = _run(tmp_path, free=120 * GIB, ifree=194394)
        assert reason == (
            "'site': not upgraded, because its image unpacks to about "
            "115,000 files and only 194394 inodes are free on %s; run "
            "'canasta image prune' or remove files, then upgrade again"
            % store)

    def test_a_filesystem_without_an_inode_count_is_not_refused(self, tmp_path):
        reason, _, _ = _run(tmp_path, free=120 * GIB, itotal=0, ifree=0)
        assert reason == ""

    def test_inodes_are_checked_when_the_image_cannot_be_inspected(
            self, tmp_path):
        reason, _, _ = _run(tmp_path, present=(), ifree=1000)
        assert "inodes are free" in reason

    def test_an_image_that_fits_goes_ahead(self, tmp_path):
        reason, _, _ = _run(tmp_path, free=8 * GIB)
        assert reason == ""

    def test_a_release_already_on_the_host_is_not_measured(self, tmp_path):
        reason, df_calls, _ = _run(tmp_path, present=(CURRENT, TARGET))
        assert reason == ""
        assert df_calls == []

    def test_a_tag_that_can_move_is_measured_even_when_present(self, tmp_path):
        reason, _, _ = _run(tmp_path, current="ghcr.io/canastawiki/canasta:latest",
                            present=("ghcr.io/canastawiki/canasta:latest",))
        assert "not upgraded" in reason

    def test_an_image_that_cannot_be_inspected_goes_ahead(self, tmp_path):
        reason, _, _ = _run(tmp_path, present=())
        assert reason == ""

    def test_an_image_store_not_on_this_filesystem_goes_ahead(self, tmp_path):
        reason, df_calls, _ = _run(tmp_path, store="/nonexistent/docker")
        assert reason == ""
        assert df_calls == []


SNAPSHOTTER = '[["driver-type","io.containerd.snapshotter.v1"]]'


class TestContainerdImageStore:
    def _root(self, tmp_path):
        root = tmp_path / "containerd"
        root.mkdir()
        return root

    def test_containerd_root_is_measured(self, tmp_path):
        root = self._root(tmp_path)
        reason, df_calls, _ = _run(
            tmp_path, driver=SNAPSHOTTER,
            containerd_conf='root = "%s"\n[plugins]\n  root = "/x"\n' % root)
        assert "is free on %s;" % root in reason
        assert df_calls and all(c.endswith(str(root)) for c in df_calls)

    def test_overlay2_store_is_still_measured(self, tmp_path):
        root = self._root(tmp_path)
        reason, _, store = _run(
            tmp_path, driver='[["Backing Filesystem","extfs"]]',
            containerd_conf='root = "%s"\n' % root)
        assert "is free on %s;" % store in reason

    def test_containerd_run_by_dockerd_is_inside_the_store(self, tmp_path):
        root = self._root(tmp_path)
        store = tmp_path / "images"
        (store / "containerd" / "daemon").mkdir(parents=True)
        reason, _, _ = _run(tmp_path, driver=SNAPSHOTTER, store=store,
                            containerd_conf='root = "%s"\n' % root)
        assert "is free on %s;" % store in reason


class TestTheUpgradeSkipsTheInstance:
    def _single(self):
        with open(SINGLE) as f:
            return yaml.safe_load(f)

    def _named(self, name):
        return next(t for t in self._single() if t.get("name") == name)

    def test_the_check_runs_before_the_upgrade_role(self):
        names = [t.get("name") for t in self._single()]
        assert names.index("Check that the image pull fits on the disk") < (
            names.index("Run upgrade role (migrations + refresh + restart)"))

    def test_the_check_is_compose_only_and_skips_source_builds(self):
        when = " ".join(self._named(
            "Check that the image pull fits on the disk")["when"])
        assert "instance_is_k8s" in when
        assert "buildFrom" in when

    def test_a_refusal_is_recorded_and_skips_the_instance(self):
        record = self._named("Skip an instance whose image would not fit")
        assert "_upgrade_space_reason" in str(record["ansible.builtin.set_fact"])
        decide = self._named("Decide whether the instance upgrades")
        assert "_upgrade_space_reason" in str(decide["ansible.builtin.set_fact"])

    def test_the_reason_is_reset_for_each_instance(self):
        names = [t.get("name") for t in self._single()]
        assert names.index("Reset the image space check for this instance") < (
            names.index("Check that the image pull fits on the disk"))
