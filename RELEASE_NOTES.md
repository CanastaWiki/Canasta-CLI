Canasta CLI version history (Ansible-based, 4.0.0+):

For earlier versions (0.1.0-alpha through 3.7.0, Go-based), see
https://github.com/CanastaWiki/Canasta-Go/blob/main/RELEASE_NOTES.md

- 4.0.0 - May 9, 2026 - Complete rewrite from Go to Ansible; replaces Canasta-Go 3.x (end-of-life as of 3.7.0). Existing `conf.json` registries and instance directories continue to work without modification; installer replaces the Go binary in place via `get-canasta.sh`. New capabilities: SSH-based multi-host management (`--host` flag, `canasta host add`/`remove`/`list`); multi-node Kubernetes with `ReadWriteMany` PVCs for multi-replica web pods; `canasta doctor`, `canasta install`/`uninstall`, `canasta storage setup nfs`/`efs`; auto-generated CLI reference on canasta.wiki. Feature parity otherwise with 3.6.3.
- 4.0.1 - May 9, 2026 - Fix `CANASTA_ENABLE_OBSERVABILITY=true`: declare `bcrypt` in `requirements.txt` so the OpenSearch Dashboards basicauth password hash gets generated.
