# learnings.md — fix-mysql-env-defaults

## What was done

Added 4 MySQL bundled-DB default entries (`MYSQL_HOST=db`, `MYSQL_PORT=3306`, `MYSQL_USER=root`, `MYSQL_SSL=false`) to the core `.env` loop in `roles/create/tasks/_env_update.yml`.

## Why

`podman-compose` doesn't resolve `${VAR:-default}` syntax in `docker-compose.yml`, so these defaults must be pinned explicitly in the `.env` file. The `canasta_env` module's `state=set` is idempotent — external-DB users' `-e` envfile values (which are merged before `_env_update.yml` runs) will override these defaults.

## Placement

Entries were inserted after the `CANASTA_IMAGE` entry (line 55) and before `no_log: true` (old line 56, now line 64), with a comment explaining the rationale.

## Observation

The reported `}}` typo on the `CANASTA_IMAGE` line was **not present** in the actual file — it already had the correct `{{ _base_image }}"` syntax. The task spec's note about the typo appears to be stale/wrong.
## F1 Audit: Plan Compliance

### Key learning: `canasta_env` module's `state=set` is NOT idempotent

- `set_line()` (canasta_env.py:105) rewrites the first occurrence of a key with the new value
- The `if old_raw != value` check (line 275) prevents writes only when the value is identical — it does NOT preserve existing values from envfile merges
- To preserve envfile values, use `state=read` first to check if a key exists, or guard write operations with a `when` condition

### Execution order matters

- `_envfile.yml` runs at main.yml line 116 (Step 7)
- `_env_update.yml` runs at main.yml line 141 (Step 9)
- The `canasta_env` module writes to .env file on disk, not in-memory state — each call reads, modifies, and writes back
- A later `state=set` for the same key will overwrite an earlier value, regardless of which task file issued it

### Always verify module behavior before relying on claimed properties

- The plan assumed `canasta_env state=set` was "idempotent" in the sense of preserving existing values
- In reality, it's only idempotent in the sense that setting the same key=value twice is a no-op
- Different values ALWAYS overwrite
