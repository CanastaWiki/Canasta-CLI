# Jinja filter for the PHP-FPM pool limits.
#
# Loaded via the `filter_plugins` path in ansible.cfg (role-local plugin
# auto-discovery does not fire under include_role / include_tasks).

import importlib.util
import os


def _load_rules():
    # Controller-side plugins cannot import ansible.module_utils.*, so the
    # shared rules are loaded from their file.
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                        "roles", "common", "module_utils", "canasta_php_fpm.py")
    spec = importlib.util.spec_from_file_location(
        "_canasta_php_fpm_for_filters", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_rules = _load_rules()


def php_fpm_pool_error(values, known_keys):
    """'' for a pool PHP-FPM accepts, else why not. known_keys is
    canasta_known_keys, whose defaults are the image's."""
    defaults = {k["name"]: k.get("default", "") for k in known_keys
                if k["name"] in _rules.POOL_KEYS}
    return _rules.pool_error(values or {}, defaults)


class FilterModule(object):
    def filters(self):
        return {"php_fpm_pool_error": php_fpm_pool_error}
