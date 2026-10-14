# Jinja filters for Kubernetes Secrets.
#
# Loaded via the `filter_plugins` path in ansible.cfg (role-local plugin
# auto-discovery does not fire under include_role / include_tasks).

import base64


def canasta_overlay_local_secrets(resources, local):
    """Replace cluster Secrets with the data the instance's files give them.

    ``resources`` is a k8s_info Secret listing; ``local`` maps a Secret name
    to its string data (as applied with ``stringData``). Each Secret named in
    ``local`` is dropped from ``resources`` and added back carrying that
    data, base64-encoded the way the API server stores it, so its ``data``
    matches what the cluster holds once the local values are applied.
    """
    local = local or {}
    kept = [r for r in resources or []
            if ((r or {}).get("metadata") or {}).get("name") not in local]
    built = []
    for name in sorted(local):
        data = {}
        for key, value in (local[name] or {}).items():
            text = "" if value is None else str(value)
            data[key] = base64.b64encode(text.encode("utf-8")).decode("ascii")
        built.append({"metadata": {"name": name}, "type": "Opaque",
                      "data": data})
    return kept + built


class FilterModule(object):
    def filters(self):
        return {
            "canasta_overlay_local_secrets": canasta_overlay_local_secrets,
        }
