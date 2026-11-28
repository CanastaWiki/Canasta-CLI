"""Direct command implementations that bypass Ansible.

Commands registered here run as pure Python, avoiding the ~3-5s
overhead of ansible-playbook startup for simple operations.

Layout:

  _helpers.py       — shared infrastructure (env/compose/ssh/registry
                      helpers, public registry decorator)
  info.py           — list, version, status
  config.py         — config get
  lifecycle.py      — stop
  maintenance.py    — maintenance script/extension/update
  host.py           — host list/add/remove
  gitops.py         — gitops status/diff
  extension_skin.py — extension/skin list
  backup.py         — backup list
  doctor.py         — doctor
  rebuild.py        — rebuild
  image.py          — image build-args
  wiki_check.py     — wiki-check

Handler modules look up shared helpers through the `_helpers` module, so
a test patches a helper there (`direct_commands._helpers._ssh_run`).
"""

# The API canasta.py uses.
from ._helpers import (  # noqa: F401
    DIRECT_COMMANDS,
    FALLBACK,
    AnsibleHandoff,
    is_direct_command,
    register,
    run_direct_command,
)

# Each handler module registers its commands at import time via the
# @register decorator from _helpers.
from . import info        # noqa: F401
from . import config      # noqa: F401
from . import lifecycle   # noqa: F401
from . import maintenance  # noqa: F401
from . import host        # noqa: F401
from . import gitops      # noqa: F401
from . import extension_skin  # noqa: F401
from . import backup      # noqa: F401
from . import doctor      # noqa: F401
from . import rebuild     # noqa: F401
from . import image       # noqa: F401
from . import wiki_check  # noqa: F401
