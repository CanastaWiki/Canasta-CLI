
## F1 Audit: Plan compliance — REJECTED

### Critical bug: MySQL defaults overwrite external-DB envfile values

**Problem:** The plan's "Why this works" section (lines 96-98) claims `canasta_env` module's `state=set` is idempotent. This is **incorrect** — `set_line()` in `canasta_env.py` (line 105) unconditionally replaces the first occurrence of the key when the value differs.

**Execution flow for external-DB case:**
1. `_envfile.yml` (main.yml:116) writes `MYSQL_HOST=myhost` from user's `-e` envfile
2. `_env_update.yml` (main.yml:141) overwrites it with `MYSQL_HOST=db`
3. Validation block (lines 98-114) finds `MYSQL_HOST=db` (not empty) → passes incorrectly
4. `COMPOSE_PROFILES` is set to empty (no bundled DB container)
5. Web container connects to `MYSQL_HOST=db` which was never started → connection failure / Varnish 503

**Impact:** External-DB setups are broken by this change. The exact symptom this fix was meant to prevent appears for the external-DB case instead.

**Fix required:** Guard the MySQL defaults with `when: not (_ext_db_check.found and _ext_db_check.value | lower == 'true')` — either by splitting into a separate task or adding a condition to the loop entries.
