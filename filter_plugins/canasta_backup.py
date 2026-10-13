# Jinja filters mapping a backup tag to the form stored on its snapshots.
#
# `backup create -t <tag>` stores <tag>__on__<hostname>, so a tag the
# operator gives to a later command has to go through the same mapping to
# match the snapshots it names.
#
# Loaded via the `filter_plugins` path in ansible.cfg (see canasta_crowdsec.py).

HOST_SEPARATOR = "__on__"


def backup_stored_tag(tag, hostname):
    """The tag `backup create` stores for <tag> taken on <hostname>."""
    return "%s%s%s" % (tag, HOST_SEPARATOR, hostname)


def backup_tag_match(tag, hostname):
    """The stored tag an operator-given <tag> refers to on <hostname>.

    A tag already in stored form names its host itself and is used as is.
    """
    if HOST_SEPARATOR in tag:
        return tag
    return backup_stored_tag(tag, hostname)


class FilterModule:
    def filters(self):
        return {
            "backup_stored_tag": backup_stored_tag,
            "backup_tag_match": backup_tag_match,
        }
