"""fix-sysctl command — permanently set unprivileged port floor to 80."""

import os
import subprocess
import sys

from . import _helpers
from ._helpers import register


_FIX_SCRIPT = r"""
pf=$(cat /proc/sys/net/ipv4/ip_unprivileged_port_start 2>/dev/null || echo "?")
echo "PORT_FLOOR:$pf"
if [ "$pf" = "?" ]; then
  exit 0
fi
if [ "$pf" -le 80 ] 2>/dev/null; then
  exit 0
fi
if sudo sysctl -w net.ipv4.ip_unprivileged_port_start=80 >/dev/null 2>&1; then
  echo "SYSCTL_OK"
else
  echo "SYSCTL_FAILED"
  exit 1
fi
if echo 'net.ipv4.ip_unprivileged_port_start=80' | sudo tee /etc/sysctl.d/canasta-privport.conf >/dev/null 2>&1; then
  echo "PERSIST_OK"
else
  echo "PERSIST_FAILED"
fi
"""


@register("fix_sysctl")
def cmd_fix_sysctl(args):
    host = getattr(args, "host", None)
    inst_id = getattr(args, "id", None)

    inst = None
    if inst_id:
        conf_path = os.path.join(_helpers._get_config_dir(), "conf.json")
        instances = _helpers._read_registry(conf_path)
        if inst_id not in instances:
            print(
                "Error: Instance '%s' not found in registry" % inst_id,
                file=sys.stderr,
            )
            return 1
        inst = instances[inst_id]
    else:
        _, inst = _helpers._resolve_instance_by_cwd(args)

    if not host and inst:
        host = inst.get("host") or "localhost"

    if host and host != "localhost":
        hostname = host
        rc, stdout = _helpers._ssh_run(host, _FIX_SCRIPT)
        if rc != 0 and not stdout.strip():
            print("Error: failed to connect to %s" % host, file=sys.stderr)
            return 1
    else:
        hostname = "localhost"
        try:
            result = subprocess.run(
                ["bash", "-c", _FIX_SCRIPT],
                capture_output=True, text=True, timeout=30,
            )
            stdout = result.stdout
        except (subprocess.TimeoutExpired, OSError) as e:
            print("Error: %s" % e, file=sys.stderr)
            return 1

    port_floor = "?"
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("PORT_FLOOR:"):
            port_floor = line[len("PORT_FLOOR:"):]

    if port_floor == "?":
        print(
            "Error: Could not read /proc/sys/net/ipv4/ip_unprivileged_port_start "
            "(not Linux?).",
            file=sys.stderr,
        )
        return 1

    port_val = int(port_floor) if port_floor.isdigit() else 0
    if port_val <= 80:
        print("Unprivileged port floor is already %s on %s — nothing to fix." % (port_floor, hostname))
        return 0

    if "SYSCTL_FAILED" in stdout:
        print(
            "Error: Could not set net.ipv4.ip_unprivileged_port_start=80 with sysctl "
            "on %s (do you have passwordless sudo?)." % hostname,
            file=sys.stderr,
        )
        print("To fix manually:", file=sys.stderr)
        print("  sudo sysctl net.ipv4.ip_unprivileged_port_start=80", file=sys.stderr)
        print("  echo 'net.ipv4.ip_unprivileged_port_start=80' | sudo tee /etc/sysctl.d/canasta-privport.conf", file=sys.stderr)
        return 1

    if "SYSCTL_OK" in stdout and "PERSIST_OK" in stdout:
        print(
            "Fixed unprivileged port floor on %s:\n"
            "  Runtime:  net.ipv4.ip_unprivileged_port_start=80 "
            "(sysctl -w, active now)\n"
            "  Persistent: /etc/sysctl.d/canasta-privport.conf "
            "(survives reboot)" % hostname
        )
        return 0

    if "PERSIST_FAILED" in stdout:
        print(
            "Warning: Runtime fix applied on %s, but could not write "
            "/etc/sysctl.d/canasta-privport.conf (do you have passwordless sudo?).\n"
            "The fix is active now but will not survive a reboot. To persist manually:\n"
            "  echo 'net.ipv4.ip_unprivileged_port_start=80' | "
            "sudo tee /etc/sysctl.d/canasta-privport.conf" % hostname
        )
        return 1

    print("Unexpected output from fix script:\n%s" % stdout, file=sys.stderr)
    return 1
