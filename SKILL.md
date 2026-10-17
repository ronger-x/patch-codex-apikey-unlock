---
name: patch-codex-apikey-unlock
description: |
  Patch ChatGPT Codex (macOS/Windows) to unlock features in API key mode.
  Support both ChatGPT and Codex branding, show the latest hidden models
  returned by app-server, expose reasoning effort levels declared by models
  but hidden by experiment configuration, and remove API key gates from
  Fast/Speed, Browser, and Computer Use.
metadata:
  version: 4.6.0
---

# Patch ChatGPT Codex - Unlock API Key Mode Features

## Overview

Remove desktop UI gates that still depend on ChatGPT sign-in context when using API key mode.

Verified with Windows Store versions `26.707.3748.0`, `26.930.4958.0`, and `26.1007.9446.0`, and macOS app versions `26.707.31428` and `26.1013.31256` (Apple Silicon). The display name and main entry point have changed to ChatGPT, but package names and directories may still use Codex. The script reads the MSIX manifest or macOS `Info.plist` as appropriate and retains fallback support for the old name.

### Unlocked Features

| # | Feature | Original restriction | Patch method |
|---|---------|----------------------|--------------|
| 1 | Latest model list | API key mode has no ChatGPT hidden-model allowlist | Show every model returned by app-server |
| 2 | Hidden reasoning effort options | API key mode has no ChatGPT experiment context | Show every option the model declares in `supportedReasoningEfforts` |
| 3 | Fast/Speed UI | Older gates require ChatGPT/PAT authentication | Accept API keys and null auth with `requiresAuth === false`; retain capability checks |
| 4 | Fast request tier | Request construction checks authentication again | Accept API keys and no-login custom providers only for service-tier resolution |
| 5 | Plugins | Older versions restrict non-ChatGPT sessions; newer versions support API key mode natively | Patch older versions; skip automatically on newer versions |
| 6 | Browser / Chrome | API key mode has no Statsig user context | Enable the desktop gate while retaining platform and app-server capability checks |
| 7 | Computer Use | Availability and the Node runtime are restricted by Statsig gates | Enable both gates while retaining native service authentication and TCC checks |
| 8 | Voice input/dictation | ChatGPT mode only | Expand to `chatgpt \|\| apikey` |
| 9 | Usage/billing settings | ChatGPT mode only | Expand to `chatgpt \|\| apikey` |
| 10 | i18n localization | API key mode has no Statsig user context | Force enablement |

## Prerequisites

- Python 3
- Node.js (the script pins `@electron/asar@3.4.1` and `@electron/fuses@1.8.0` to avoid the Node 22.12+ requirement in newer releases)

## Quick Start (Recommended)

```bash
# macOS / Windows; paths are detected automatically
python3 patch.py

# Check/debug only the JS patches without repacking app.asar
python3 patch.py --assets /path/to/webview/assets

# Dry run without writing any files
python3 patch.py --dry-run
```

`patch.py` performs every step automatically: stop processes -> detect the installation type -> create an independent copy -> extract/repack ASAR -> apply JS patches -> handle Windows fuses/shortcuts or update macOS ASAR integrity and signatures.

## macOS

The script checks `/Applications` and `~/Applications` in order for `ChatGPT.app` / `Codex.app`, reads the actual executable from `Info.plist`, and requires a Codex runtime marker in Resources (the `codex`/`codex.exe` file, or `com.openai.codex.manifest`, a file in earlier builds and a directory from 26.1013). The official app is never modified. The patched copy is written beside it, with fallback to `~/Applications` if that directory is not writable. The copy is named `ChatGPT-Codex-Patched.app` for the new brand and `Codex-Patched.app` for the old brand.

### One-Command Patch

```bash
python3 patch.py

# Custom installation and output paths
python3 patch.py --app /path/to/Codex.app \
  --output "$HOME/Applications/ChatGPT-Codex-Patched.app"
```

On completion, the script refreshes the `ElectronAsarIntegrity` header hash in `Info.plist`, applies an ad-hoc signature only to the outermost app, and strictly verifies the entire bundle. Signing removes application-group/keychain entitlements that are valid only for the OpenAI Team ID while preserving runtime permissions for JIT, audio, camera, networking, automation, and similar features. From 26.1013 the runtime framework embeds a digest of the whole `ElectronAsarIntegrity` dictionary (`__asar_integrity` section in `Codex Framework`); a stale digest kills start-up with `Failed to get integrity for validatable asar archive`. The workflow recomputes the digest over the updated plist entries (SHA-256 of sorted key + algorithm + hash), rewrites the 40+ byte slot behind the `AGbevlPCksUGKNL8TSn7wGmJEuJsXb2A` sentinel when the packaged digest is `used`/version 1, and ad-hoc re-signs that framework together with its Electron helpers. The helpers enforce library validation when they `dlopen` the re-signed framework, so each helper (the `.app` bundles under the framework plus `app_mode_loader` and `web_app_shortcut_copier`) is re-signed with its original entitlements minus OpenAI Team-ID application groups plus `disable-library-validation`. Nested code deeper than one level and unrelated frameworks such as Sparkle keep their OpenAI signatures; the outer entitlement also disables library validation so these different signatures load. This weakens dynamic-library injection protection for the outer process and its helpers, so this treatment is used only for the independent copy.

### Browser and Computer Use

Renderer-computed desktop availability determines whether the bundled Browser/Chrome/Computer Use plugins are enabled. When an API key session lacks ChatGPT Statsig user context, the original app records `reason=statsig-disabled` and removes the plugins from the runtime marketplace. The patch locates the three gates by `featureName` and availability fields; it does not treat numeric Statsig IDs or model names as patch semantics. The original logic still performs platform, WSL, app-server experimental feature, and plugin configuration checks. The Computer Use Node runtime gate is handled separately.

On macOS, Computer Use runs the OpenAI-signed `Codex Computer Use.app` with bundle ID `com.openai.sky.CUAService`. In System Settings, the permission entry is named **Codex Computer Use**. Check both "Accessibility" and "Screen & System Audio Recording" (named "Screen Recording" on older systems), rather than looking for ChatGPT. Its service, client, `codex`, `node_repl`, and Node runtime all retain their OpenAI signatures.

The outer patched copy must carry an ad-hoc signature. When the macOS responsible-process check attributes the Browser native addon to this outer process, it returns `missing-code-signing-identity`. The patch downgrades only this result to allowed, continues to reject `untrusted-code-signing-identity` and `missing-socket-file-descriptor`, and sets the Browser socket to owner-only mode `0600`; it does not modify native `.node` modules. The security tradeoff is that unsigned processes running as the same user lose some isolation. The official app remains unchanged as a fallback for Appshots and the TCC identity of the original main app.

### Rollback

```bash
rm -rf /Applications/ChatGPT-Codex-Patched.app
rm -rf "$HOME/Applications/ChatGPT-Codex-Patched.app"
# Old brand: rm -rf /Applications/Codex-Patched.app
```

The official app retains its original signature and remains directly usable.


---

## Windows

### Rollback

**Store version (MSIX, ChatGPT brand):**
```powershell
# Delete the patched directory directly; the original Store version is unchanged
Remove-Item -Recurse -Force "$env:LOCALAPPDATA\Programs\ChatGPT-Codex-Patched"
```

**Traditional installer:**
```powershell
cd "$env:LOCALAPPDATA\Programs\Codex\resources"
Remove-Item -Recurse -Force app -ErrorAction SilentlyContinue
if (Test-Path app.asar1) { Rename-Item app.asar1 app.asar }
if (Test-Path app.asar.bak) { Copy-Item app.asar.bak app.asar }
Write-Host "Rolled back to the original version"
```

### One-Command Patch (Windows)

```powershell
python3 patch.py
```

> Automatically detect the Microsoft Store version (MSIX) or traditional installation. Read the MSIX entry point from `AppxManifest.xml` and verify the Codex resource marker.
> Copy the ChatGPT-branded Store version to `%LOCALAPPDATA%\Programs\ChatGPT-Codex-Patched` without modifying the original, and create the desktop shortcut `ChatGPT Codex (Patched)`.

The Store build embeds the SHA-256 hash of the original `app.asar` header in
the Electron executable. Repacking without updating that embedded value causes
Electron to exit immediately with `Integrity check failed for asar archive`.
The workflow updates the hash only in the copied executable and gives the
patched shortcut an independent `--user-data-dir` to avoid the official app's
single-instance lock.
Changing the copied executable also invalidates its original vendor
Authenticode signature by design; the official Store installation is not
modified and retains its signature.

---

## config.toml

Preserve the existing API provider configuration. The patch does not hard-code model IDs, reasoning effort levels, or the legacy `features.enable_fast` setting. Models and reasoning effort levels come from the app-server `model/list` response (requested with `includeHidden: true`), and only the options that each model actually declares in `supportedReasoningEfforts` are shown. Fast selection uses the current version's `service_tier`.

---

## Troubleshooting Version Updates

After ChatGPT Codex updates, both JS filenames (hash suffixes) and variable names may change. Use the following methods to locate each patch target.

In Windows `26.930.4958.0`, Fast UI/request gates also accept `personalAccessToken`; preserve that branch when adding API key support. The taskbar identity helper moved into `bootstrap-*.js` and uses a nested flavor enum. Match the chunk containing `setAppUserModelId`, since `worker.js` contains a similar helper that must remain unchanged. Large generated JS payloads require literal prefilters and identifier boundaries to avoid excessive regex backtracking.

### General Search Strategy

```bash
# Use --assets to check/apply only the JS patches
python3 patch.py --assets /Applications/ChatGPT-Codex-Patched.app/Contents/Resources/app/webview/assets
# Windows (Store version)
python3 patch.py --assets "$env:LOCALAPPDATA\Programs\ChatGPT-Codex-Patched\resources\app\webview\assets"

# Manual search (macOS)
cd /Applications/ChatGPT-Codex-Patched.app/Contents/Resources/app/webview/assets
```

### 1. Latest Model List

```bash
grep -rl "useHiddenModels" *.js
# In version 26.707, the target is model-list-filter-*.js.
# Original condition: if(useAllowlist?allowed.has(model.model):!model.hidden)
# New condition applies only to API key mode:
# if(authMethod===`apikey`||(useAllowlist?allowed.has(model.model):!model.hidden))
```

### 2. Hidden Reasoning Effort Options

```bash
grep -rl "enabledReasoningEfforts" *.js
# There are two filtering layers in the same model-list-filter-*.js:
# 1. Remove ultra when includeUltraReasoningEffort is false
# 2. Hide disabled levels with enabledReasoningEfforts.has(effort)
# API key mode bypasses both layers but retains validation against legal enum values.
# Do not hard-code effort levels; show each model's supportedReasoningEfforts.
```

The full API key mode list appears in the Advanced/Effort menu. Options such as Max and Ultra appear only when the current model actually declares support. The simplified Power slider uses fixed combinations from the official app and is not modified by this patch.

For a custom provider with `requires_openai_auth = false`, `account/read` can return `account: null` and `getAuthStatus` can return `authMethod: null`. The frontend preserves that null value. Reasoning unlocks must therefore also accept a null authentication method when `isCustomModelProvider` is true; an `authMethod === apikey` check alone leaves the experiment filters active. Preserve the original behavior for other authentication modes and for a null method without a custom provider.

### 3. Fast UI / Service Tier Gate

Locate the complete authorization assignment and its adjacent authentication-method assignment. Older chunks return `isServiceTierAllowed`; `26.1007` returns a `serviceTierAccess` map in `app-initial-*.js`. Consumer chunks also reference these fields, so require the complete gate expression and a unique target before editing.

```bash
rg -l 'isServiceTierAllowed|serviceTierAccess|canUseFastMode' -g '*.js'
```

Extend the existing ChatGPT/PAT condition with `auth?.authMethod === 'apikey'` and `(auth?.authMethod == null && auth?.requiresAuth === false)`. A provider with `requires_openai_auth = false` can return `account: null`, so checking only `apikey` still hides Fast. Retain the hook's loading and capability checks, and recognize earlier API-key-only patches as inputs that need this upgrade.

The model's `serviceTiers` supplies the available speed options. `configRequirements/read` supplies server restrictions such as `featureRequirements.fast_mode` and `ultrafast_mode`. Preserve both: enabling authentication must not invent a Fast tier or override an explicit server denial.

### 4. Fast Request Gate

Find the service-tier request function and preserve its original rejection value. Older builds return `false`; `26.1007` returns `null` before resolving the access map. Add `apikey` to the existing ChatGPT/PAT allowlist without changing that return type.

```bash
rg -l 'Failed to read service tier for request' -g '*.js'
```

In the `26.1007` merged chunk, the request helper shares its async account resolver with plugin queries. Add a parameter that defaults to `false`, and enable it only at the service-tier call site. With that parameter enabled, map `account == null && requiresOpenaiAuth === false` to the local `apikey` branch; preserve the resolver's default behavior for plugin queries, ChatGPT, PAT, Copilot, Bedrock, and unauthenticated sessions that still require OpenAI login.

Validate the UI and request paths together. Cover null and undefined auth, explicit `requiresAuth` values, loading state, models with and without Fast, server denial, migration from earlier patches, and repeated execution. Windows `26.1007.9446.0` passed 36 tests and 467 subtests on 2026-10-15; the full rebuild applied 12 patches with no failures, and extracted real functions returned Fast access for local no-login provider data while still rejecting a simulated server denial.

After an assets-only check, run the complete workflow to repack `app.asar` and update the embedded Windows hash, then relaunch the patched shortcut. Verify the packed code and matching integrity hash before reporting success. Function-level checks and a responsive renderer establish compatibility and startup; they do not establish model response speed or a completed billable Fast request.

### 5. Plugins

```bash
grep -rl "useHiddenOpenAICuratedMarketplaces" *.js
# Version 26.707 use-plugins-*.js already contains:
# return method!==`chatgpt`&&method!==`apikey`&&...
# This means API key mode is supported natively; the script should report SKIP
# instead of applying an incorrect patch.
# On the plugin detail page, connector-unavailable text is used only to explain the reason.
```

If an older version still has an assignment gate in `check-plugin-availability-*.js`, the script continues to apply the compatible `false&&` patch. This UI patch does not forge the ChatGPT session identity required by `/aip/connectors`, so do not describe newer ChatGPT session-based connectors as natively available with an API key.

### 6. Browser / Chrome

```bash
grep -rl 'featureName:`browser_use`' *.js
# The same desktop availability chunk should contain both:
# featureName:`browser_use`          + isBrowserAgentGateEnabled
# featureName:`browser_use_external` + isExternalBrowserUseGateEnabled
# The script traces each availability field back to its Statsig assignment and ORs
# the original gate with authMethod === `apikey`; both hooks always execute.
# It does not depend on minified variable names or use the current Statsig ID as a locator.

# The macOS Browser native pipe fallback is in .vite/build/main-*.js beside webview:
grep -rl 'browser-use-peer-authorization.node' ../../.vite/build/main-*.js
# Allow only reason === `missing-code-signing-identity`.
# Continue to reject `untrusted-code-signing-identity` and a missing socket fd.
```

### 7. Computer Use

```bash
grep -rl 'featureName:`computer_use`' *.js
# In desktop availability, locate the Statsig assignment for isComputerUseGateEnabled.

grep -rl 'computerUseNodeRepl' *.js
# The computerUseNodeRepl field synchronized to the main process has a separate runtime gate.
# The script ORs the original gate with API key mode without bypassing computer.available.
# Other authentication modes continue to use the original gate.
# 26.1013 removed this field; the patched availability gate already covers
# the whole frontend computer-use path, so the runtime patch reports absent.
```

During runtime verification, the marketplace should contain `computer-use`, and the OpenAI-signed `SkyComputerUseService` should create the group-container socket. The TCC entry is named `Codex Computer Use`. Do not bypass service authentication by modifying native binaries or using `codesign --deep`.

### 8. Voice Input

```bash
grep -rn "authMethod===.chatgpt." *.js | grep -v "!=="
# Find xxx&&yyy.authMethod===`chatgpt` in annotation/comment/editor-related files.
# Expand to xxx&&(yyy.authMethod===`chatgpt`||yyy.authMethod===`apikey`)
```

### 9. Usage Settings

```bash
grep -rn "let.*===.chatgpt." *.js
# Find let r=e===`chatgpt` in usage-settings-related files.
# Expand to let r=e===`chatgpt`||e===`apikey`
```

### 10. i18n Localization

```bash
grep -rn "enable_i18n" *.js
# Find xxx=(0,YYY.useMemo)(()=>nnn?.get(`enable_i18n`,!1),[nnn])
# Change to xxx=(0,YYY.useMemo)(()=>!0,[nnn])
# Key point: without user context, the Statsig experiment gate returns false by default.
```

## How It Works

| Operation | Reason | Location |
|-----------|--------|----------|
| Read the entry point from the manifest/plist | The ChatGPT display name, package identity, and executable name no longer match | Installation discovery |
| Repack `app.asar` after extraction | The Owl runtime does not fall back to loading a loose `app/` directory | Resources directory |
| Update the Windows embedded ASAR hash | Electron validates the repacked archive before starting the renderer | Copied `ChatGPT.exe` (Windows only) |
| Preserve `.node` / native module sidecars | Electron must load native extensions from disk | `app.asar.unpacked` |
| Update `ElectronAsarIntegrity` | Match the repacked ASAR header hash to `Info.plist` | `Info.plist` (macOS only) |
| Refresh the framework digest slot | Starting with 26.1013, Electron compares a packaged digest of the whole integrity dictionary and dies on mismatch | `Codex Framework` binary (macOS only, 26.1013+) |
| Apply an outer ad-hoc signature and strict verification | Allow the modified copy to launch while preserving internal OpenAI signatures | Final step (macOS only) |
| Bypass hidden-model filtering | API key mode has no ChatGPT Statsig model allowlist | `model-list-filter-*` |
| Bypass reasoning-effort filtering | API key mode has no ChatGPT experiment context, so the enabled set may hide model-declared options | `model-list-filter-*` |
| Allow request-level service tiers | Showing the Fast option alone does not change actual requests | `read-service-tier-for-request-*` |
| Enable the desktop availability gate | Bundled plugins are removed when API key mode has no Statsig user context | Desktop feature chunk |
| Enable the Computer Use runtime gate | A Node runtime must still be selected after availability is established | Desktop feature sync chunk |
| Fall back when the Browser signing identity is missing | An ad-hoc outer app prevents complete responsible-process identity resolution | `.vite/build/main-*` (macOS only) |
| Bypass Statsig experiments | API key mode has no Statsig user context, so i18n/feature experiments default to disabled | webview JS |
| Bypass `authMethod` gates | Multiple features check `=== 'chatgpt'`, excluding API key mode | webview JS |
