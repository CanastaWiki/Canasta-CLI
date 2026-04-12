"""Unit tests for the K8s node resource preflight script.

The script is invoked by k8s_preflight.yml against the output of
`kubectl get nodes -o jsonpath` reading .status.capacity, and checks
whether at least one node meets the configured minimums.

See #58 for the rationale behind checking capacity rather than
allocatable, and the 4 GiB total memory threshold.
"""

import os
import subprocess
import sys

import pytest

SCRIPT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..",
    "..",
    "roles",
    "orchestrator",
    "files",
    "scripts",
    "check_node_resources.py",
)


def run_script(node_data, min_cpu="600", min_mem="3500", min_stor="15"):
    """Run check_node_resources.py with the given env, return (rc, stdout)."""
    env = os.environ.copy()
    env["NODE_DATA"] = node_data
    env["MIN_CPU_MILLI"] = min_cpu
    env["MIN_MEMORY_MI"] = min_mem
    env["MIN_STORAGE_GI"] = min_stor
    proc = subprocess.run(
        [sys.executable, SCRIPT],
        env=env,
        capture_output=True,
        text=True,
    )
    return proc.returncode, proc.stdout


class TestCheckNodeResources:
    def test_single_node_sufficient_passes(self):
        # 4 vCPU, 8 GiB, 30 GiB ephemeral — comfortably above defaults
        node_data = "node-1\t4\t8Gi\t30Gi"
        rc, out = run_script(node_data)
        assert rc == 0
        assert "OK" in out
        assert "node-1" in out

    def test_real_4gib_cloud_instance_passes(self):
        # AWS c7i-flex.large reports ~3826 MiB capacity (advertised 4 GiB).
        # The threshold must accommodate this reality. See #65.
        node_data = "node-1\t2\t3826Mi\t20Gi"
        rc, out = run_script(node_data)
        assert rc == 0
        assert "OK" in out

    def test_single_node_insufficient_memory_fails(self):
        # 2 GiB total — t3.small case (~1900 MiB capacity in practice)
        node_data = "node-1\t2\t2Gi\t30Gi"
        rc, out = run_script(node_data)
        assert rc == 1
        assert "INSUFFICIENT" in out

    def test_real_2gib_cloud_instance_fails(self):
        # AWS t3.small reports ~1900 MiB capacity (advertised 2 GiB).
        # Must still be rejected by the new threshold.
        node_data = "node-1\t2\t1900Mi\t30Gi"
        rc, out = run_script(node_data)
        assert rc == 1
        assert "INSUFFICIENT" in out

    def test_single_node_insufficient_storage_fails(self):
        node_data = "node-1\t2\t8Gi\t10Gi"
        rc, out = run_script(node_data)
        assert rc == 1
        assert "INSUFFICIENT" in out

    def test_single_node_insufficient_cpu_fails(self):
        # 500m CPU — below the 600m default minimum
        node_data = "node-1\t500m\t8Gi\t30Gi"
        rc, out = run_script(node_data)
        assert rc == 1
        assert "INSUFFICIENT" in out

    def test_any_node_qualifying_is_enough(self):
        # First node fails, second node passes — overall pass
        node_data = "node-1\t2\t2Gi\t30Gi\nnode-2\t4\t8Gi\t30Gi"
        rc, out = run_script(node_data)
        assert rc == 0
        assert "node-1" in out and "INSUFFICIENT" in out
        assert "node-2" in out and "OK" in out

    def test_no_nodes_qualifying_fails(self):
        node_data = "node-1\t2\t2Gi\t30Gi\nnode-2\t2\t3Gi\t30Gi"
        rc, _ = run_script(node_data)
        assert rc == 1

    def test_empty_node_data_fails(self):
        rc, out = run_script("")
        assert rc == 1
        assert "No node data" in out

    def test_memory_unit_parsing(self):
        # All units that kubectl can emit for capacity.memory.
        # Threshold is 3500 MiB (#65) so values are picked relative to that.
        test_cases = [
            ("4194304Ki", True),  # 4 GiB in Ki, comfortably above threshold
            ("4096Mi", True),  # 4 GiB in Mi
            ("4Gi", True),  # 4 GiB literal
            ("3826Mi", True),  # c7i-flex.large reality
            ("3500Mi", True),  # exactly at threshold
            ("3499Mi", False),  # 1 MiB below threshold
            ("3Gi", False),  # 3 GiB literal, below threshold
            ("2097152Ki", False),  # 2 GiB in Ki, well below threshold
        ]
        for mem_str, expected_pass in test_cases:
            node_data = f"node-1\t4\t{mem_str}\t30Gi"
            rc, out = run_script(node_data)
            if expected_pass:
                assert rc == 0, f"Expected {mem_str} to pass: {out}"
            else:
                assert rc == 1, f"Expected {mem_str} to fail: {out}"

    def test_default_min_memory_is_3500(self):
        # If MIN_MEMORY_MI is not in the environment at all, the script
        # should default to 3500 MiB (#65). Test by clearing the var and
        # using a node just below that.
        env = os.environ.copy()
        env["NODE_DATA"] = "node-1\t4\t3499Mi\t30Gi"
        env.pop("MIN_MEMORY_MI", None)
        env["MIN_CPU_MILLI"] = "600"
        env["MIN_STORAGE_GI"] = "15"
        proc = subprocess.run(
            [sys.executable, SCRIPT],
            env=env,
            capture_output=True,
            text=True,
        )
        # 3499Mi < 3500Mi default, so should fail
        assert proc.returncode == 1, (
            "Default MIN_MEMORY_MI should be 3500; got: %s" % proc.stdout
        )

        # And just at the threshold should pass
        env["NODE_DATA"] = "node-1\t4\t3500Mi\t30Gi"
        proc = subprocess.run(
            [sys.executable, SCRIPT],
            env=env,
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, (
            "3500Mi should exactly meet the default threshold; got: %s"
            % proc.stdout
        )
