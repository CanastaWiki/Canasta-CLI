#!/bin/sh
# Make MediaWiki's database account match .env, inside the db container.
#
# Run with the wiki database set as arguments, either as root in the bundled
# db container, fed on stdin:
#   sh -s -- <db> [<db>...] < ensure_wiki_db_account.sh
# or in a container beside it with DB_HOST (and optionally DB_PORT) naming
# the database server, which it then reaches over TCP (the Kubernetes web
# pod's ensure-db-account init container, or the web container for an
# external database). It reads WIKI_DB_USER, WIKI_DB_PASSWORD and the
# administrator's password from the container's environment, so no secret is
# on a command line. The administrator is DB_ADMIN_USER with
# DB_ADMIN_PASSWORD, or root with MYSQL_ROOT_PASSWORD when those are unset.
#
# The account '<user>'@'%' gets ALL PRIVILEGES on exactly the named
# databases and nothing else. Grants on databases no longer named are
# revoked. Each name is escaped for GRANT, where _ and % are wildcards.
#
# Prints one line per outcome for the caller:
#   SKIPPED <reason>      nothing to do (account is the administrator's)
#   ENSURED <user>        account created or updated and verified
#   UNDECLARED <db>       a database exists that the account cannot use
# Exits non-zero, with the error on stderr, when the account cannot be set.

set -eu

admin_user=${DB_ADMIN_USER:-root}
admin_password=${DB_ADMIN_PASSWORD:-${MYSQL_ROOT_PASSWORD:-}}
user=${WIKI_DB_USER:-}
if [ -z "$user" ] || [ "$user" = root ] || [ "$user" = "$admin_user" ]; then
    echo "SKIPPED account is the administrator's"
    exit 0
fi
case $user in
    *[!A-Za-z0-9_]*)
        echo "WIKI_DB_USER '$user' may only contain letters, digits and _" >&2
        exit 2 ;;
esac
if [ -z "${WIKI_DB_PASSWORD:-}" ]; then
    echo "WIKI_DB_PASSWORD is empty; refusing to set an empty password" >&2
    exit 2
fi
for db in "$@"; do
    case $db in
        ''|*[!A-Za-z0-9_-]*)
            echo "invalid database name '$db'" >&2
            exit 2 ;;
    esac
done

# client <password> <mariadb args...>
client() {
    pw_=$1
    shift
    if [ -n "${DB_HOST:-}" ]; then
        MYSQL_PWD="$pw_" mariadb -h "$DB_HOST" -P "${DB_PORT:-3306}" "$@"
    else
        MYSQL_PWD="$pw_" mariadb "$@"
    fi
}
admin() { client "$admin_password" -u "$admin_user" "$@"; }
sql_string() { printf '%s' "$1" | sed "s/\\\\/\\\\\\\\/g; s/'/''/g"; }
grant_pattern() { printf '%s' "$1" | sed 's/[_%]/\\&/g'; }

pw=$(sql_string "$WIKI_DB_PASSWORD")
account="'$user'@'%'"

{
    printf "CREATE USER IF NOT EXISTS %s IDENTIFIED BY '%s';\n" "$account" "$pw"
    printf "ALTER USER %s IDENTIFIED BY '%s';\n" "$account" "$pw"
    for db in "$@"; do
        printf 'GRANT ALL PRIVILEGES ON `%s`.* TO %s;\n' "$(grant_pattern "$db")" "$account"
    done
} | admin

# mysql.db holds each grant's database pattern as written, so compare the
# escaped forms.
wanted=
for db in "$@"; do
    wanted="$wanted $(grant_pattern "$db")"
done
admin -N --raw -e "SELECT Db FROM mysql.db WHERE User='$user' AND Host='%'" |
while IFS= read -r granted; do
    case " $wanted " in
        *" $granted "*) ;;
        *) printf 'REVOKE ALL PRIVILEGES ON `%s`.* FROM %s;\n' "$granted" "$account" ;;
    esac
done | admin

existing=$(admin -N -e "SHOW DATABASES")

# Verify the account can reach every named database that exists. A wiki's
# database can be named before it is created (create grants before
# install.php runs), so a missing one is not an error.
for db in "$@"; do
    if printf '%s\n' "$existing" | grep -qx -- "$db"; then
        client "$WIKI_DB_PASSWORD" -u "$user" -N -e "SELECT 1" "$db" >/dev/null
    fi
done

printf '%s\n' "$existing" | while IFS= read -r db; do
    case $db in
        information_schema|mysql|performance_schema|sys) continue ;;
    esac
    case " $* " in
        *" $db "*) ;;
        *) echo "UNDECLARED $db" ;;
    esac
done

echo "ENSURED $user"
