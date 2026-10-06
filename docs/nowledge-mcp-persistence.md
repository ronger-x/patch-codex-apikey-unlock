# Keeping Nowledge MCP configuration across Codex configuration rewrites

Investigated on 2026-10-06 with Windows, Nowledge plugin 0.1.38, nmem 0.10.88,
and CC Switch 3.20.4. The application patch does not manage MCP credentials.

## Cause and evidence limits

The affected user-level `~/.codex/config.toml` had no
`mcp_servers.nowledge-mem` entry. The enabled Nowledge plugin supplied its default
`http://127.0.0.1:14242/mcp` endpoint, although nmem itself was configured for a
remote server. `nmem config mcp show --host codex` prints the correct configuration;
it does not install that configuration into Codex.

CC Switch was also managing Codex configuration. Its
`~/.cc-switch/cc-switch.db` database had no Nowledge MCP record. CC Switch removes
MCP entries when saving provider snapshots and projects enabled MCP servers from
its database after writing a provider configuration. A repair made only in the
Codex file can therefore disappear when CC Switch rewrites that file.

This is a confirmed mechanism for losing the remote override. The available
logs did not identify the process responsible for each historical deletion.
There is no evidence that the Codex updater itself deleted the setting.

## Persistent configuration

1. Obtain the generated configuration with:

   ```text
   nmem --json config mcp show --host codex
   ```

   Parse the TOML in the `rendered` or `config` field. Output can contain API keys;
   do not publish it in logs, issues, or source control.

2. Back up `~/.codex/config.toml`, then merge the generated Nowledge server into
   that user-level configuration. Preserve unrelated providers, servers, and
   preferences.

3. If CC Switch manages Codex, also add or update `nowledge-mem` in its MCP
   management interface and enable it for Codex. Keep this record aligned with
   the generated configuration whenever the endpoint or credentials change.
   Updating a provider snapshot does not replace this step.

4. Keep the Nowledge plugin enabled for its skills and hooks. Do not edit the
   versioned plugin cache's `.mcp.json` as a permanent fix. The plugin installer
   preserves a user-owned MCP block outside its managed markers.

CC Switch's HTTP server specification uses `type = http` and `headers`, which it
converts to Codex's `http_headers`. Preserve `env_http_headers` as an additional
field. The generated configuration used these dynamic identity mappings:

| Header | Environment variable |
|--------|----------------------|
| `X-Nmem-Agent-Id` | `NMEM_AGENT_ID` |
| `X-Nmem-Host-Agent-Id` | `NMEM_HOST_AGENT_ID` |
| `X-Nmem-Space-Id` | `NMEM_SPACE` |

It also included `x-nmem-space-protocol = exact-v1`,
`X-Nmem-Tool-Set = external-agent`, and
`X-Nowledge-Tool-Schema-Profile = slim`. Prefer freshly generated values to a
manually maintained header list.

## Repair and verification performed

The investigated installation was repaired by merging the generated user-level
configuration and transactionally adding the missing CC Switch MCP record.
Only `enabled_codex` was enabled. Existing MCP records, provider snapshots, and
CC Switch settings were checked for equality before and after the change.

An ordinary file backup protected the Codex configuration. The SQLite backup API
created a consistent CC Switch database backup before the transaction. Keep
these backups in the user profile because they can contain credentials. Example
backup naming conventions are:

```text
~/.codex/config.toml.<timestamp>.pre-nowledge-sync.bak
~/.cc-switch/backups/pre_nowledge_mcp_<timestamp>.db
```

Verification included:

- An independent database connection read the new record successfully, and
  `PRAGMA quick_check` returned `ok`.
- A temporary-file simulation of CC Switch 3.20.4's projection started from a
  provider snapshot without MCP entries. The resulting URL, authentication and
  protocol headers, and environment-header mappings matched the repaired Codex
  configuration. Other provider fields remained unchanged. This was a
  source-based simulation, not a live provider switch.
- `codex mcp list/get` found one `nowledge-mem` using the configured remote URL.
- A fresh isolated Codex app-server 0.160.1 returned one `nowledge-mem` from
  `mcpServerStatus/list`, with `authStatus = bearerToken` and 16 tools.
- Direct MCP `initialize` and `tools/list` requests succeeded with 16 tools.
- The Codex configuration and CC Switch record still matched after patching and
  launching the desktop application.

Use a fresh session or isolated app-server to verify configuration changes.
An existing session's already initialized MCP client is not evidence that the
new configuration has loaded.

## CC Switch source references

These links are pinned to the inspected `v3.20.4` release:

- [codex_config.rs:3782](https://github.com/farion1231/cc-switch/blob/v3.20.4/src-tauri/src/codex_config.rs#L3782):
  MCP is database-owned and stripped from provider snapshots.
- [services/provider/live.rs:1532](https://github.com/farion1231/cc-switch/blob/v3.20.4/src-tauri/src/services/provider/live.rs#L1532):
  MCP projection follows application configuration writes.
- [services/mcp.rs:250](https://github.com/farion1231/cc-switch/blob/v3.20.4/src-tauri/src/services/mcp.rs#L250):
  Projection reads the current MCP database records and their enabled states.
- [database/dao/mcp.rs:11](https://github.com/farion1231/cc-switch/blob/v3.20.4/src-tauri/src/database/dao/mcp.rs#L11):
  MCP table fields and database reads.
- [mcp/codex.rs:687](https://github.com/farion1231/cc-switch/blob/v3.20.4/src-tauri/src/mcp/codex.rs#L687):
  HTTP `headers` become Codex `http_headers`.
- [mcp/codex.rs:706](https://github.com/farion1231/cc-switch/blob/v3.20.4/src-tauri/src/mcp/codex.rs#L706):
  Additional fields such as `env_http_headers` survive conversion.
