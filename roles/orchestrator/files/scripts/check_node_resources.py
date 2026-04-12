#!/usr/bin/env python3
"""Check that at least one Kubernetes node meets minimum resource requirements.

Reads node capacity data from NODE_DATA env var (tab-separated lines of
name, cpu, memory, ephemeral-storage as output by kubectl jsonpath against
.status.capacity) and compares against MIN_CPU_MILLI, MIN_MEMORY_MI, and
MIN_STORAGE_GI.

Capacity, not allocatable. Allocatable is what
kubelet will schedule (capacity minus kube-reserved minus system-reserved
minus eviction-threshold), and on a 4 GiB node it's ~3.5 GiB. Checking
against capacity makes the threshold match what the user sees as their
instance size.

Exits 0 if any node qualifies, 1 otherwise.  Prints a per-node summary to
stdout so Ansible can include it in its failure message.
"""

import os
import re
import sys


def parse_cpu(value):
    """Return CPU in millicores."""
    if value.endswith("m"):
        return int(value[:-1])
    return int(float(value) * 1000)


def parse_memory(value):
    """Return memory in MiB."""
    units = {"Ki": 1024, "Mi": 1024**2, "Gi": 1024**3}
    for suffix, factor in units.items():
        if value.endswith(suffix):
            return int(float(value[: -len(suffix)]) * factor / (1024**2))
    # Plain bytes
    return int(int(value) / (1024**2))


def parse_storage(value):
    """Return ephemeral storage in GiB."""
    units = {"Ki": 1024, "Mi": 1024**2, "Gi": 1024**3}
    for suffix, factor in units.items():
        if value.endswith(suffix):
            return float(value[: -len(suffix)]) * factor / (1024**3)
    # Plain bytes
    return int(value) / (1024**3)


def main():
    node_data = os.environ.get("NODE_DATA", "").strip()
    min_cpu = int(os.environ.get("MIN_CPU_MILLI", 600))
    # 3500 MiB rather than a round 4096 because cloud instances
    # marketed as "4 GiB" report ~3700-3900 MiB capacity.
    min_mem = int(os.environ.get("MIN_MEMORY_MI", 3500))
    min_stor = int(os.environ.get("MIN_STORAGE_GI", 15))

    if not node_data:
        print("No node data available")
        sys.exit(1)

    any_qualified = False
    lines = []
    for line in node_data.splitlines():
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        name, cpu_raw, mem_raw, stor_raw = parts[0], parts[1], parts[2], parts[3]
        cpu = parse_cpu(cpu_raw)
        mem = parse_memory(mem_raw)
        stor = parse_storage(stor_raw)
        ok = cpu >= min_cpu and mem >= min_mem and stor >= min_stor
        if ok:
            any_qualified = True
        lines.append(
            f"  {name}: cpu={cpu}m mem={mem}Mi storage={stor:.1f}Gi "
            f"{'OK' if ok else 'INSUFFICIENT'}"
        )

    print("\n".join(lines))
    sys.exit(0 if any_qualified else 1)


if __name__ == "__main__":
    main()
