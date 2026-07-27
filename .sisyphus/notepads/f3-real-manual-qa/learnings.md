# Learnings

## canasta_env.py `state=set` behavior
- **Overwrites** existing keys (first occurrence replaced, duplicates dropped)
- Appends if key doesn't exist
- Idempotent: compares raw value (quotes intact) against new value; no write if identical
- Uses `parse_env_lines()` + `set_line()` for the rewrite path (preserves verbatim lines)

## Create flow task ordering
- Step 7: `_envfile.yml` — merges custom `-e envfile` into `.env`
- Step 8: Generate `wikis.yaml`
- Step 9: `_env_update.yml` — sets passwords, MW_SITE_*, MYSQL_*, secret key
- So envfile merge runs BEFORE env update ✓

## The 4 MYSQL entries
- MYSQL_HOST = "db"
- MYSQL_PORT = "3306"
- MYSQL_USER = "root"
- MYSQL_SSL = "false"
- These are podman-compose compatibility defaults (podman-compose can't resolve `${VAR:-default}` syntax)
- Intended to only apply for internal DB, but currently unconditional (see issues.md)