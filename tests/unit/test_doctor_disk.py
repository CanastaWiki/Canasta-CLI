"""Doctor measures free space where container images are stored.

Rootless Podman and rootless Docker keep images under the user's home,
often a different filesystem from `/`, so free space on `/` said nothing
about whether the next pull fits.
"""

from direct_commands.doctor import (
    _disk_line,
    _parse_system_df,
    _reclaimable_line,
)


def test_image_store_on_root_reads_as_root():
    assert _disk_line("12G|/") == "12G on /"


def test_image_store_elsewhere_is_named():
    assert _disk_line("40G|/home/mah/.local/share/containers/storage") == (
        "40G on /home/mah/.local/share/containers/storage "
        "(container image storage)")


def test_unusable_probe_reads_as_unknown():
    assert _disk_line("unknown") == "unknown"
    assert _disk_line("|/") == "unknown"


def test_podman_system_df_is_read_like_dockers():
    # `podman system df` has no Build Cache row.
    podman_df = ("Images|3.2GB (64%)\n"
                 "Containers|0B (0%)\n"
                 "Local Volumes|1.1GB (100%)")
    assert _parse_system_df(podman_df) == ("3.2GB", "0B")
    assert _reclaimable_line(podman_df, "MISSING") == (
        "  Reclaimable:     3.2GB images, 0B build cache — reclaim images "
        "with 'canasta image prune'")


def test_containerd_root_on_another_filesystem_is_named():
    assert _disk_line("291G|/data/docker|5.1G|/var/lib/containerd") == (
        "291G on /data/docker (Docker data root), "
        "5.1G on /var/lib/containerd (container image storage)")


def test_empty_containerd_fields_read_as_before():
    assert _disk_line("40G|/data/docker||") == (
        "40G on /data/docker (container image storage)")
