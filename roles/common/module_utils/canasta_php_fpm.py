# -*- coding: utf-8 -*-
"""The PHP-FPM pool limits the web image's dynamic pool must satisfy.

PHP-FPM refuses to start, and the wiki answers 503, when the PM_* values
it is given break these limits.
"""

from __future__ import absolute_import, division, print_function

__metaclass__ = type

POOL_KEYS = ("PM_MAX_CHILDREN", "PM_START_SERVERS",
             "PM_MIN_SPARE_SERVERS", "PM_MAX_SPARE_SERVERS")


def pool_error(values, defaults):
    """'' when the pool PHP-FPM gets is valid, else why not.

    values: the PM_* values .env sets (a key .env does not set is absent
    or empty). defaults: the value the image uses for each key .env does
    not set.
    """
    effective = {}
    for key in POOL_KEYS:
        raw = str(values.get(key) or "").strip()
        source = "" if raw else " (default)"
        raw = raw or str(defaults[key])
        try:
            number = int(raw)
        except ValueError:
            return "%s is '%s', not a whole number." % (key, raw)
        if number < 1:
            return "%s is %s; it must be at least 1." % (key, number)
        effective[key] = (number, source)

    def show(key):
        number, source = effective[key]
        return "%s=%d%s" % (key, number, source)

    children = effective["PM_MAX_CHILDREN"][0]
    start = effective["PM_START_SERVERS"][0]
    min_spare = effective["PM_MIN_SPARE_SERVERS"][0]
    max_spare = effective["PM_MAX_SPARE_SERVERS"][0]
    if max_spare > children:
        problem = "PM_MAX_SPARE_SERVERS cannot be greater than PM_MAX_CHILDREN"
    elif min_spare > max_spare:
        problem = ("PM_MIN_SPARE_SERVERS cannot be greater than "
                   "PM_MAX_SPARE_SERVERS")
    elif not min_spare <= start <= max_spare:
        problem = ("PM_START_SERVERS must be between PM_MIN_SPARE_SERVERS "
                   "and PM_MAX_SPARE_SERVERS")
    else:
        return ""
    return ("PHP-FPM would refuse this pool and the wiki would answer 503: "
            "%s. The pool would be %s. Set the keys together, for example "
            "PM_MAX_CHILDREN=12 PM_MAX_SPARE_SERVERS=8 PM_START_SERVERS=6."
            % (problem, ", ".join(show(k) for k in POOL_KEYS)))
