"""Doctor reports reclaimable image space without counting volumes.

`docker system df` calls a volume reclaimable when no container references
it. `canasta stop` runs `docker compose down`, which removes the containers,
so every stopped instance's database volume qualifies. A reclaimable figure
that included the Local Volumes row would present a wiki's database as
recoverable disk.
"""

from direct_commands.doctor import (
    _is_zero_size,
    _parse_system_df,
    _reclaimable_line,
)

NO_K3S = "MISSING"

# A host with one stopped instance: 9.9GB of "reclaimable" volume is that
# instance's database.
DF_WITH_VOLUMES = (
    "Images|5.1GB (60%)\n"
    "Containers|0B (0%)\n"
    "Local Volumes|9.9GB (100%)\n"
    "Build Cache|210MB"
)


def test_volumes_are_never_counted():
    assert _parse_system_df(DF_WITH_VOLUMES) == ("5.1GB", "210MB")


def test_volume_size_never_reaches_the_reported_line():
    line = _reclaimable_line(DF_WITH_VOLUMES, NO_K3S)
    assert "9.9GB" not in line
    assert "5.1GB images" in line
    assert "210MB build cache" in line


def test_nothing_reclaimable_reports_none_despite_reclaimable_volumes():
    df = "Images|0B (0%)\nLocal Volumes|9.9GB (100%)\nBuild Cache|0B"
    assert _reclaimable_line(df, NO_K3S) == "  Reclaimable:     none"


def test_missing_rows_default_to_zero():
    assert _parse_system_df("Images|1.5GB (30%)") == ("1.5GB", "0B")


def test_no_docker_daemon_omits_the_line_on_a_compose_host():
    assert _reclaimable_line("unknown", NO_K3S) is None
    assert _reclaimable_line("", NO_K3S) is None


def test_zero_size_detection():
    assert _is_zero_size("0B")
    assert _is_zero_size("0B (0%)")
    assert not _is_zero_size("5.1GB")
    # An unparseable figure is reported rather than silently called zero.
    assert not _is_zero_size("unknown")


def test_build_cache_points_at_docker_builder_prune():
    line = _reclaimable_line(
        "Images|10.09GB (95%)\nBuild Cache|6.3GB", NO_K3S)
    assert ("reclaim images with 'canasta image prune', build cache "
            "with 'docker builder prune'") in line


def test_build_cache_alone_does_not_name_image_prune():
    line = _reclaimable_line("Images|0B (0%)\nBuild Cache|6.3GB", NO_K3S)
    assert "canasta image prune" not in line
    assert "docker builder prune" in line
