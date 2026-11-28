# -*- coding: utf-8 -*-
"""Per-instance maintenance schedules (canasta schedule).

config/schedules.yml maps each schedule name to its cron expression, the
service whose container runs it, the shell command, and optionally the
gitops hosts it is limited to. Each active schedule is one line in a
crontab block owned by the instance; the line runs `canasta schedule run`,
which reads the command from the file when it fires, so nothing from the
file but the validated name and cron fields ever reaches the crontab.
"""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

import re
import shlex

import yaml

SCHEDULES_FILE = "config/schedules.yml"
STATE_DIR = ".schedule-state"
RESERVED_NAMES = ("backup",)
ENTRY_KEYS = ("cron", "service", "command", "hosts")

_NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,38}[a-z0-9])?\Z")
# Same as backup_schedule_validate.yml: five plain cron fields.
_CRON_RE = re.compile(r"^[0-9A-Za-z*/,-]+([ \t]+[0-9A-Za-z*/,-]+){4}\Z")
_SERVICE_RE = re.compile(r"^[a-z0-9]([a-z0-9_.-]*[a-z0-9])?\Z")
_HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?\Z")
# cron runs jobs with PATH=/usr/bin:/bin, which lacks the tools canasta runs
# from /usr/local/bin (canasta itself, as get-canasta.sh installs it).
CRON_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def validate_name(name):
    if not isinstance(name, str) or not _NAME_RE.match(name):
        return ("schedule name '%s' must be 1-40 lowercase letters, digits "
                "or hyphens, starting and ending with a letter or digit"
                % name)
    if name in RESERVED_NAMES:
        return ("schedule name '%s' is reserved; use 'canasta backup "
                "schedule' for backups" % name)
    return None


def validate_cron(expr):
    if not isinstance(expr, str) or not _CRON_RE.match(expr.strip()):
        return ("cron expression must have exactly 5 fields (minute hour "
                "day month weekday), got: '%s'" % expr)
    return None


def validate_entry(name, entry):
    """Error strings for one schedule entry; empty when it is valid."""
    errors = []
    err = validate_name(name)
    if err:
        return [err]
    if not isinstance(entry, dict):
        return ["schedule '%s' must be a mapping" % name]
    unknown = sorted(set(entry) - set(ENTRY_KEYS))
    if unknown:
        errors.append("schedule '%s' has unknown keys: %s"
                      % (name, ", ".join(unknown)))
    err = validate_cron(entry.get("cron"))
    if err:
        errors.append("schedule '%s': %s" % (name, err))
    service = entry.get("service", "web")
    if not isinstance(service, str) or not _SERVICE_RE.match(service):
        errors.append("schedule '%s': invalid service '%s'" % (name, service))
    command = entry.get("command")
    if not isinstance(command, str) or not command.strip():
        errors.append("schedule '%s': command must be a non-empty string"
                      % name)
    hosts = entry.get("hosts")
    if hosts is not None:
        if (not isinstance(hosts, list) or not hosts
                or not all(isinstance(h, str) and _HOST_RE.match(h)
                           for h in hosts)):
            errors.append("schedule '%s': hosts must be a non-empty list of "
                          "gitops host names" % name)
    return errors


def parse(text):
    """Parse config/schedules.yml text into {name: entry}.

    Raises ValueError listing every problem, so a bad file that arrived
    through gitops is refused as a whole rather than half applied.
    """
    data = yaml.safe_load(text) if text and text.strip() else None
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError("%s must be a mapping of schedule names"
                         % SCHEDULES_FILE)
    errors = []
    for name, entry in data.items():
        errors.extend(validate_entry(str(name), entry))
    if errors:
        raise ValueError("invalid %s: %s" % (SCHEDULES_FILE, "; ".join(errors)))
    return {str(name): dict(entry) for name, entry in data.items()}


def dump(schedules):
    """Serialize schedules as stable YAML (sorted names and keys)."""
    out = {}
    for name in sorted(schedules):
        entry = schedules[name]
        out[name] = {k: entry[k] for k in ENTRY_KEYS if k in entry}
    return yaml.safe_dump(out, default_flow_style=False, sort_keys=False)


def parse_hosts(value):
    """A comma-separated --gitops-hosts value as a list, or None."""
    if value is None:
        return None
    if isinstance(value, list):
        items = value
    else:
        items = str(value).split(",")
    items = [h.strip() for h in items if h and h.strip()]
    return items or None


def is_active(entry, gitops_hostname):
    """Whether a schedule runs on this host.

    A schedule without hosts runs wherever the instance is. One with hosts
    runs only on a gitops host it names, so a restored clone with no
    gitops identity, or another host's, never runs it.
    """
    hosts = entry.get("hosts")
    if not hosts:
        return True
    return bool(gitops_hostname) and gitops_hostname in hosts


def block_marker(instance_id, mark):
    return "# %s canasta-schedule-%s" % (mark, instance_id)


def run_fragment(instance_id):
    """The job-line fragment that identifies this instance's schedules.

    The leading space matches whatever binary a line invokes; the trailing
    space keeps 'foo' from matching 'foo2'.
    """
    return " schedule run -i %s --name " % instance_id


def cron_line(name, entry, instance_id, instance_path, canasta_bin,
              cron_env=""):
    log = "%s/schedule-%s.log" % (instance_path.rstrip("/"), name)
    cron = " ".join(entry["cron"].split())
    job = "PATH=%s %s%s schedule run -i %s --name %s >> %s 2>&1" % (
        CRON_PATH, cron_env, shlex.quote(canasta_bin), instance_id, name,
        shlex.quote(log))
    # cron turns an unescaped % in the command into a newline.
    return "%s %s" % (cron, job.replace("%", "\\%"))


def strip_instance(text, instance_id):
    """Crontab text without this instance's schedule block or stray lines."""
    begin = block_marker(instance_id, "BEGIN")
    end = block_marker(instance_id, "END")
    fragment = run_fragment(instance_id)
    kept = []
    inside = False
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped == begin:
            inside = True
            continue
        if stripped == end:
            inside = False
            continue
        if inside or fragment in line:
            continue
        kept.append(line)
    return kept


def rewrite_crontab(text, instance_id, lines):
    """Replace this instance's schedule block with `lines`."""
    kept = strip_instance(text, instance_id)
    while kept and not kept[-1].strip():
        kept.pop()
    if lines:
        kept.append(block_marker(instance_id, "BEGIN"))
        kept.extend(lines)
        kept.append(block_marker(instance_id, "END"))
    return ("\n".join(kept) + "\n") if kept else ""


def installed(text, instance_id):
    """{name: cron expression} for this instance's crontab lines."""
    fragment = run_fragment(instance_id)
    found = {}
    for line in (text or "").splitlines():
        if line.lstrip().startswith("#") or fragment not in line:
            continue
        name = line.split(fragment, 1)[1].split()[0]
        found.setdefault(name, []).append(" ".join(line.split()[0:5]))
    return found


def backup_cron(text, instance_id):
    """The backup schedule's cron expression, or '' when none."""
    fragment = " backup create -i %s " % instance_id
    for line in (text or "").splitlines():
        if not line.lstrip().startswith("#") and fragment in line:
            return " ".join(line.split()[0:5])
    return ""


def describe_state(state):
    if not state:
        return "never", "-"
    result = state.get("result", "?")
    if result == "failed":
        result = "exit %s" % state.get("rc", "?")
    return str(state.get("started", "?")), result


def render_table(schedules, crontab, states, instance_id, gitops_hostname):
    """The `canasta schedule list` output."""
    found = installed(crontab, instance_id)
    rows = []
    backup = backup_cron(crontab, instance_id)
    if backup:
        rows.append(("backup", "-", backup, "-", "-", "(canasta backup schedule)"))
    warnings = []
    for name in sorted(schedules):
        entry = schedules[name]
        last, result = describe_state(states.get(name))
        rows.append((name, entry.get("service", "web"),
                     " ".join(entry["cron"].split()), last, result,
                     entry["command"]))
        active = is_active(entry, gitops_hostname)
        if active and name not in found:
            warnings.append(
                "Schedule '%s' is not installed in this host's crontab; run "
                "'canasta gitops pull' or re-add it." % name)
        elif not active:
            warnings.append(
                "Schedule '%s' runs only on gitops host(s) %s, not on this "
                "host%s." % (name, ", ".join(entry["hosts"]),
                             (" (%s)" % gitops_hostname)
                             if gitops_hostname else ""))
        if len(found.get(name, [])) > 1:
            warnings.append("Schedule '%s' has %d crontab entries."
                            % (name, len(found[name])))
    for name in sorted(set(found) - set(schedules)):
        warnings.append(
            "Crontab entry for '%s' has no schedule in %s; run 'canasta "
            "gitops pull' or re-add and remove it." % (name, SCHEDULES_FILE))
    if not rows:
        lines = ["No schedules for instance '%s'." % instance_id]
    else:
        header = ("NAME", "SERVICE", "CRON", "LAST RUN", "RESULT", "COMMAND")
        widths = [max(len(str(r[i])) for r in rows + [header])
                  for i in range(5)]
        lines = []
        for row in [header] + rows:
            lines.append("  ".join(str(row[i]).ljust(widths[i])
                                   for i in range(5)) + "  " + str(row[5]))
    return "\n".join(lines + warnings)
