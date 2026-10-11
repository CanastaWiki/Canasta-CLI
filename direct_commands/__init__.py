"""Direct command implementations that bypass Ansible.

Commands registered here run as pure Python, avoiding the ~3-5s
overhead of ansible-playbook startup for simple operations.

Layout:

  _helpers.py       — shared infrastructure (env/compose/k8s/ssh/
                      registry helpers, public registry decorator)
  info.py           — list, version, status
  config.py         — config get
  lifecycle.py      — start, stop, scale
  maintenance.py    — maintenance script/extension/update
  host.py           — host list/add/remove
  gitops.py         — gitops status/diff
  extension_skin.py — extension/skin list
  backup.py         — backup list
  doctor.py         — doctor
  argocd.py         — argocd password/apps/ui
  rebuild.py        — rebuild
  wiki_check.py     — wiki-check

Test code that does `monkeypatch.setattr(direct_commands, "_ssh_run", …)`
should patch `direct_commands._helpers._ssh_run` instead — the helper
modules look up shared helpers through the `_helpers` module so a patch
on the package alone wouldn't propagate.
"""

# Public API + helper-module symbols. canasta.py uses the public API;
# test code reaches into direct_commands for private symbols (e.g.
# _ssh_run, _read_env_file, _resolve_instance).
from ._helpers import (  # noqa: F401
    DIRECT_COMMANDS,
    FALLBACK,
    AnsibleHandoff,
    is_direct_command,
    register,
    run_direct_command,
)
from ._helpers import *  # noqa: F401, F403

# Underscore-prefixed names aren't pulled by `*`. Import the ones test
# code references as `direct_commands._foo`.
from ._helpers import (  # noqa: F401
    _SENTINEL,
    _MAINT_PATH_RE,
    _check_dir_exists,
    _check_running_compose,
    _check_running_k8s,
    _compose_file_args,
    _dump_compose_failure,
    _exec_in_container,
    _filter_by_host,
    _gather_instance_info,
    _host_matches,
    _lint_env_content,
    _make_detail,
    _parse_env_entries,
    _print_table,
    _read_env_file,
    _read_registry,
    _read_wikis,
    _resolve_instance,
    _script_arg_tokens,
    _shell_quote,
    _ssh_run,
    _sync_compose_profiles,
    _write_env_content,
    _write_registry,
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
from . import wiki_check  # noqa: F401

# Per-handler symbols re-exported so test code can reach
# direct_commands.cmd_list, direct_commands.cmd_doctor, etc.
from .info import (  # noqa: F401
    cmd_list,
    cmd_version,
    cmd_status,
    _gather_all_instances,
)
from .config import cmd_config_get  # noqa: F401
from .lifecycle import cmd_start, cmd_stop, cmd_scale  # noqa: F401
from .maintenance import (  # noqa: F401
    cmd_maintenance_script,
    cmd_maintenance_extension,
    cmd_maintenance_update,
)
from .host import cmd_host_list, cmd_host_add, cmd_host_remove  # noqa: F401
from .gitops import (  # noqa: F401
    cmd_gitops_status,
    cmd_gitops_diff,
    _git_ssh_env_prefix,
    _gitops_argocd_status,
    _gitops_diff_script,
    _gitops_status_script,
    _parse_gitops_diff,
    _parse_gitops_status,
    _parse_gitops_status_k8s,
    _parse_remote_sync,
)
from .extension_skin import cmd_extension_list, cmd_skin_list  # noqa: F401
from .backup import cmd_backup_list  # noqa: F401
from .doctor import cmd_doctor, _parse_doctor  # noqa: F401
from .argocd import (  # noqa: F401
    cmd_argocd_password,
    cmd_argocd_apps,
    cmd_argocd_ui,
)
from .rebuild import cmd_rebuild, _list_buildable_services  # noqa: F401
from .wiki_check import cmd_wiki_check  # noqa: F401
