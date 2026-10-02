#!/bin/sh
# Make MediaWiki's database account match .env, inside the db container.
#
# Run as root in the bundled db container (or pod), fed on stdin, with the
# wiki database set as arguments:
#   sh -s -- <db> [<db>...] < ensure_wiki_db_account.sh
# It reads WIKI_DB_USER, WIKI_DB_PASSWORD and MYSQL_ROOT_PASSWORD from the
# container's environment, so no secret is on a command line.
#
# The account '<user>'@'%' gets ALL PRIVILEGES on exactly the named
# databases and nothing else. Grants on databases no longer named are
# revoked. Each name is escaped for GRANT, where _ and % are wildcards.
#
# Prints one line per outcome for the caller:
#   SKIPPED <reason>      nothing to do (account is root, or no password)
#   ENSURED <user>        account created or updated and verified
#   UNDECLARED <db>       a database exists that the account cannot use
# Exits non-zero, with the error on stderr, when the account cannot be set.

set -eu

user=${WIKI_DB_USER:-}
if [ -z "$user" ] || [ "$user" = root ]; then
    echo "SKIPPED account is root"
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

root() { MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mariadb -u root "$@"; }
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
} | root

# mysql.db holds each grant's database pattern as written, so compare the
# escaped forms.
wanted=
for db in "$@"; do
    wanted="$wanted $(grant_pattern "$db")"
done
root -N --raw -e "SELECT Db FROM mysql.db WHERE User='$user' AND Host='%'" |
while IFS= read -r granted; do
    case " $wanted " in
        *" $granted "*) ;;
        *) printf 'REVOKE ALL PRIVILEGES ON `%s`.* FROM %s;\n' "$granted" "$account" ;;
    esac
done | root

existing=$(root -N -e "SHOW DATABASES")

# Verify the account can reach every named database that exists. A wiki's
# database can be named before it is created (create grants before
# install.php runs), so a missing one is not an error.
for db in "$@"; do
    if printf '%s\n' "$existing" | grep -qx -- "$db"; then
        MYSQL_PWD="$WIKI_DB_PASSWORD" mariadb -u "$user" -N -e "SELECT 1" "$db" >/dev/null
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
