"""Direct command implementations that bypass Ansible.

Commands registered here run as pure Python, avoiding the ~3-5s
overhead of ansible-playbook startup for simple operations.

This package was split out of a single 2510-line file. Layout:

  _helpers.py       — shared infrastructure (env/compose/k8s/ssh/
                      registry helpers, public registry decorator)
  info.py           — list, version, status
  config.py         — config get
  lifecycle.py      — start, stop, restart, scale
  maintenance.py    — maintenance script/extension/update
  host.py           — host list/add/remove
  gitops.py         — gitops status/diff
  extension_skin.py — extension/skin list
  backup.py         — backup list
  doctor.py         — doctor
  argocd.py         — argocd password/apps/ui

Test code that does `monkeypatch.setattr(direct_commands, "_ssh_run", …)`
should patch `direct_commands._helpers._ssh_run` instead — the helper
modules look up shared helpers through the `_helpers` module so a patch
on the package alone wouldn't propagate.
"""

# Public API + helper-module symbols re-exported for backward compat.
# Test code and canasta.py both reach into direct_commands for many
# private symbols (e.g. _ssh_run, _read_env_file, _resolve_instance);
# preserve those import paths.
from ._helpers import (  # noqa: F401
    DIRECT_COMMANDS,
    FALLBACK,
    is_direct_command,
    register,
    run_direct_command,
)
from ._helpers import *  # noqa: F401, F403

# Underscore-prefixed names aren't pulled by `*`. Import them explicitly
# so existing `direct_commands._foo` references keep working.
from ._helpers import (  # noqa: F401
    _SENTINEL,
    _MAINT_PATH_RE,
    _MANAGED_PROFILES,
    _check_dir_exists,
    _check_running,
    _check_running_compose,
    _check_running_k8s,
    _compose_file_args,
    _dump_compose_failure,
    _entries_to_content,
    _exec_in_container,
    _filter_by_host,
    _gather_instance_info,
    _gather_k8s,
    _gather_local,
    _get_config_dir,
    _get_script_dir,
    _host_matches,
    _hosts_yml_path,
    _is_localhost,
    _k8s_get_pod,
    _make_detail,
    _normalize_script_args,
    _parse_env_entries,
    _print_table,
    _read_env_content,
    _read_env_file,
    _read_hosts_yml,
    _read_registry,
    _read_remote_or_local_file,
    _read_wikis,
    _resolve_instance,
    _resolve_ssh_target,
    _run_compose,
    _set_env_entry,
    _shell_quote,
    _ssh_args,
    _ssh_run,
    _stream_in_container,
    _sync_compose_profiles,
    _write_env_content,
    _write_hosts_yml,
    _write_registry,
    _write_remote_or_local_file,
)

# Side-effect imports — each handler module registers its commands at
# import time via the @register decorator from _helpers.
from . import info        # noqa: F401
from . import config      # noqa: F401
from . import lifecycle   # noqa: F401
from . import maintenance  # noqa: F401
from . import host        # noqa: F401
from . import gitops      # noqa: F401
from . import extension_skin  # noqa: F401
from . import backup      # noqa: F401
from . import doctor      # noqa: F401
from . import argocd      # noqa: F401
from . import rebuild     # noqa: F401

# Per-handler symbols re-exported so test code can reach
# direct_commands.cmd_list, direct_commands.cmd_doctor, etc.
from .info import (  # noqa: F401
    cmd_list,
    cmd_version,
    cmd_status,
    _classify_for_cleanup,
    _gather_all_instances,
    _kubectl_section,
    _read_instance_image,
    _resolve_status_instance,
)
from .config import cmd_config_get  # noqa: F401
from .lifecycle import cmd_start, cmd_stop, cmd_restart, cmd_scale  # noqa: F401
from .lifecycle import _k8s_namespace, _k8s_stop, _run_kubectl  # noqa: F401
from .maintenance import (  # noqa: F401
    cmd_maintenance_script,
    cmd_maintenance_extension,
    cmd_maintenance_update,
    _read_wiki_ids,
    _resolve_wiki_targets,
)
from .host import cmd_host_list, cmd_host_add, cmd_host_remove  # noqa: F401
from .gitops import (  # noqa: F401
    cmd_gitops_status,
    cmd_gitops_diff,
    _gitops_argocd_status,
    _gitops_diff_script,
    _gitops_status_script,
    _parse_gitops_diff,
    _parse_gitops_status,
    _parse_gitops_status_k8s,
)
from .extension_skin import cmd_extension_list, cmd_skin_list  # noqa: F401
from .backup import cmd_backup_list  # noqa: F401
from .doctor import cmd_doctor, _parse_doctor  # noqa: F401
from .argocd import (  # noqa: F401
    cmd_argocd_password,
    cmd_argocd_apps,
    cmd_argocd_ui,
    _argocd_admin_password,
)
from .rebuild import cmd_rebuild, _list_buildable_services  # noqa: F401
