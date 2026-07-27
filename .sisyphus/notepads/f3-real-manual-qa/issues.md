# Issues Found

## ISSUE: Missing conditional on MYSQL_* entries in _env_update.yml

**Severity:** High — breaks external DB setups

**Location:** `roles/create/tasks/_env_update.yml`, lines 56-63

**Description:**
The 4 MYSQL entries (MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_SSL) are set **unconditionally** in the `Set core .env variables` task using `canasta_env` with `state=set`. Since `state=set` **overwrites** existing keys (confirmed in `canasta_env.py` `set_line()` function), any external DB values provided by the user via `-e envfile` are silently overwritten with internal DB defaults.

**The comment on lines 56-59 says:**
> "These are only written when the internal DB is used — external-DB setups supply their own via the -e envfile."

But there is **no `when:` clause** to enforce this. The entries are set unconditionally.

**Impact:**
1. User provides `-e envfile` with `USE_EXTERNAL_DB=true` and `MYSQL_HOST=my-db.example.com`
2. `_envfile.yml` (Step 7) merges the envfile, setting `MYSQL_HOST=my-db.example.com` in `.env`
3. `_env_update.yml` (Step 9) overwrites `MYSQL_HOST` with `"db"` (the internal default)
4. The external DB validation check (lines 98-114) reads `MYSQL_HOST` and finds `"db"` — passes the "not empty" check but with the **wrong value**
5. The instance will try to connect to the internal `db` container instead of the user's external database

**Fix:** Add a `when:` condition to only set these 4 entries when NOT using an external DB, e.g.:
```yaml
when: not (_ext_db_check.found and _ext_db_check.value | lower == 'true')
```

However, this creates a chicken-and-egg problem since `_ext_db_check` is read AFTER this task (line 91-96). The fix would require either:
- Moving the external DB check BEFORE the MYSQL_* entries
- Or splitting the loop into two: one for unconditional entries, one for DB-default entries with a `when:` guard