"""doctor checks membership of gid 33, not of a group named www-data.

The containers write files as gid 33. Debian names it www-data; RHEL has
no www-data group and names gid 33 tape. The check and its advice both
follow the host's own name for the gid.
"""

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from direct_commands import doctor  # noqa: E402
from direct_commands import _helpers  # noqa: E402

D = _helpers._SENTINEL


def _probe_line():
    lines = [ln for ln in doctor._DOCTOR_SCRIPT.splitlines()
             if "getent group 33" in ln]
    assert len(lines) == 1
    return lines[0]


def _run_probe(tmp_path, gids, group33):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "id").write_text(
        "#!/bin/sh\n[ \"$1\" = -G ] && echo '%s'\n" % gids)
    getent = "#!/bin/sh\n"
    if group33:
        getent += "[ \"$2\" = 33 ] && echo '%s:x:33:' && exit 0\n" % group33
    getent += "exit 2\n"
    (bin_dir / "getent").write_text(getent)
    for name in ("id", "getent"):
        (bin_dir / name).chmod(0o755)
    env = {"PATH": "%s:/usr/bin:/bin" % bin_dir}
    return subprocess.run(["bash", "-c", _probe_line()], capture_output=True,
                          text=True, env=env, check=True).stdout.strip()


class TestProbe:
    def test_member_on_rhel(self, tmp_path):
        assert _run_probe(tmp_path, "1000 10 33 990", "tape") == "MEMBER|tape"

    def test_not_member_on_debian(self, tmp_path):
        assert (_run_probe(tmp_path, "1000 330 133", "www-data")
                == "NOT_MEMBER|www-data")

    def test_no_group_has_gid_33(self, tmp_path):
        assert _run_probe(tmp_path, "1000", None) == "NOT_MEMBER|"

    def test_probe_is_the_last_field(self):
        stdout = (D + "\n").join(["x"] * 27 + ["MEMBER|tape"]) + "\n"
        assert doctor._doctor_probes(stdout)["web_gid"] == "MEMBER|tape"


class TestReportLine:
    def test_member_named_www_data(self):
        assert (doctor._www_data_line("user", "MEMBER|www-data")
                == "  www-data group:  OK (member)")

    def test_member_under_another_name(self):
        # Membership is by gid, so the name in `id -nG` does not matter.
        assert (doctor._www_data_line("user wheel", "MEMBER|tape")
                == "  www-data group:  OK (member of gid 33, 'tape' on this host)")

    def test_not_member_names_the_hosts_group(self):
        line = doctor._www_data_line("user wheel", "NOT_MEMBER|tape")
        assert "NOT A MEMBER" in line
        assert "sudo usermod -aG tape $USER" in line
        assert "usermod -aG www-data" not in line

    def test_not_member_on_debian(self):
        line = doctor._www_data_line("user", "NOT_MEMBER|www-data")
        assert "sudo usermod -aG www-data $USER" in line
        assert "gid 33" not in line

    def test_no_group_has_gid_33(self):
        line = doctor._www_data_line("user", "NOT_MEMBER|")
        assert "NOT A MEMBER" in line
        assert "groupadd -g 33 www-data" in line

    def test_a_www_data_name_is_not_enough(self):
        # A group named www-data with another gid does not grant access.
        line = doctor._www_data_line("user www-data", "NOT_MEMBER|tape")
        assert "NOT A MEMBER" in line

    def test_without_the_probe_falls_back_to_the_name(self):
        assert "OK (member)" in doctor._www_data_line("user www-data", "")
        assert "NOT A MEMBER" in doctor._www_data_line("user", "")

    def test_full_report_on_rhel(self):
        fields = ["Python 3.12.0"] * 28
        fields[4] = "user wheel"
        fields[13] = "Linux"
        fields[27] = "NOT_MEMBER|tape"
        report = doctor._parse_doctor((D + "\n").join(fields) + "\n", "h")
        assert "sudo usermod -aG tape $USER" in report
