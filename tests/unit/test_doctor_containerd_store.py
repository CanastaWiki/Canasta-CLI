"""Doctor reports containerd's root when Docker uses the containerd image store.

With the containerd snapshotter, images and container layers live under
containerd's root rather than DockerRootDir. The disk probe runs here
against stub `docker` and `df` commands, with the containerd config path
pointed at a temp file.
"""

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from direct_commands import doctor  # noqa: E402

SNAPSHOTTER = '[["driver-type","io.containerd.snapshotter.v1"]]'


def _probe_line():
    lines = [ln for ln in doctor._DOCTOR_SCRIPT.splitlines()
             if "DockerRootDir" in ln]
    assert len(lines) == 1
    return lines[0]


def _run(tmp_path, driver_status, config, devices):
    """Run the disk probe; devices maps a path to its df device name."""
    store = tmp_path / "docker"
    store.mkdir(exist_ok=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "docker").write_text(
        "#!/bin/sh\n"
        "case \"$*\" in\n"
        "  *DockerRootDir*) echo '%s' ;;\n"
        "  *DriverStatus*) echo '%s' ;;\n"
        "  *) exit 1 ;;\n"
        "esac\n" % (store, driver_status))
    cases = "".join("  %s) dev=%s ;;\n" % (p, d) for p, d in devices.items())
    (bin_dir / "df").write_text(
        "#!/bin/sh\n"
        "for a; do p=$a; done\n"
        "case \"$p\" in\n%s  *) dev=/dev/root ;;\nesac\n"
        "echo 'Filesystem Size Used Avail Use%% Mounted'\n"
        "echo \"$dev 10G 1G 9G-$dev 10%% /\"\n" % cases)
    for name in ("docker", "df"):
        os.chmod(bin_dir / name, 0o755)
    conf = tmp_path / "config.toml"
    conf.write_text(config)
    line = _probe_line().replace("/etc/containerd/config.toml", str(conf))
    script = ("_t() { \"$@\"; }\n_timedout() { false; }\nD=DELIM\n"
              + line.replace("%%", "%") + "\n")
    env = {"PATH": "%s:/usr/bin:/bin" % bin_dir}
    out = subprocess.run(["sh", "-c", script], capture_output=True,
                         text=True, env=env, check=True).stdout
    return out.split("DELIM")[0].strip(), str(store)


def test_containerd_root_on_another_filesystem_is_appended(tmp_path):
    root = tmp_path / "containerd"
    root.mkdir()
    out, store = _run(
        tmp_path, SNAPSHOTTER,
        'version = 2\nroot = "%s"\n[plugins]\n  root = "/nope"\n' % root,
        {str(root): "/dev/var"})
    assert out == "9G-/dev/root|%s|9G-/dev/var|%s" % (store, root)


def test_containerd_root_on_the_same_filesystem_is_not_repeated(tmp_path):
    root = tmp_path / "containerd"
    root.mkdir()
    out, store = _run(tmp_path, SNAPSHOTTER, 'root = "%s"\n' % root, {})
    assert out == "9G-/dev/root|%s" % store


def test_overlay2_store_is_unchanged(tmp_path):
    root = tmp_path / "containerd"
    root.mkdir()
    out, store = _run(tmp_path, '[["Backing Filesystem","extfs"]]',
                      'root = "%s"\n' % root, {str(root): "/dev/var"})
    assert out == "9G-/dev/root|%s" % store


def test_containerd_managed_by_dockerd_is_inside_the_data_root(tmp_path):
    root = tmp_path / "containerd"
    root.mkdir()
    (tmp_path / "docker" / "containerd" / "daemon").mkdir(parents=True)
    out, store = _run(tmp_path, SNAPSHOTTER, 'root = "%s"\n' % root,
                      {str(root): "/dev/var"})
    assert out == "9G-/dev/root|%s" % store
