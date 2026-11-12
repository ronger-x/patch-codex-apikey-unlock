# ChatGPT Codex - Full Feature Unlock for API Key Mode

This project targets the Codex desktop app whose display name has been changed to **ChatGPT**. It unlocks the model list, reasoning effort levels, service tiers, built-in image generation, and other features in API key mode.

It has been verified with Windows Store versions `26.707.3748.0`, `26.930.4958.0`, `26.1007.9446.0`, `26.1021.3705.0`, `26.1023.6621.0`, and `26.1029.7803.0`, and the macOS app versions `26.707.31428` and `26.1013.31256` (Apple Silicon). The current adapter also targets Store packages `26.1103.3517.0` and `26.1103.7773.0`, which bundle Codex CLI `0.167.0-alpha.3.2`; their JavaScript, native-signature, full-workflow, and startup-smoke checks pass. In these versions, the display name and primary entry point have changed to ChatGPT, while installation packages may still use the Codex name. The script supports both the old and new names.

## Unlocked Features

| Feature | Description |
|---------|-------------|
| Latest model list | Shows models returned by app-server that are marked as hidden by default |
| Hidden reasoning effort options | In API key mode, shows every reasoning effort level that the model actually supports but the ChatGPT experiment configuration hides |
| Fast/Speed mode | Enables API key and no-login custom-provider sessions in both the UI and request path while preserving model support and server restrictions |
| Plugins marketplace | Supports the legacy gate; automatically skips the patch when a newer version natively supports API keys |
| Browser / Chrome | Removes the desktop availability gate caused by missing Statsig context in API key mode while preserving real platform and app-server capability checks |
| Computer Use | Enables desktop availability and the Node runtime; on macOS, uses the separately packaged helper service signed by OpenAI |
| Legacy connector UI gate | Supports only the legacy frontend gate; does not impersonate a ChatGPT session-based connector identity |
| Consistent branding | Forces the relevant check to return `false`, regardless of account type |
| Voice input/dictation | Expands availability to `chatgpt \|\| apikey` |
| Usage/billing settings | Expands availability to `chatgpt \|\| apikey` |
| i18n languages | Bypasses the Statsig experiment gate and forces the feature on |
| Location privacy default | Defaults guest sessions and missing precise-location preferences to off in Data controls; preserves explicit account preferences and the existing permission flow |
| Built-in image generation | On Windows `26.1021`, `26.1023`, `26.1029`, `26.1103`, and `26.1106`, exposes `image_gen.imagegen` to eligible API-key/custom-provider sessions while retaining feature, plan, provider-capability, and model-modality checks; `26.1103` and `26.1106` reuse the `26.1029` native signature set |

## Prerequisites

- Node.js (the script pins versions of `@electron/asar` and `@electron/fuses` that remain compatible with older Node.js releases)
- Python 3

## Files

```
patch.py                  Main script: one-command workflow for both ChatGPT and Codex names (macOS / Windows)
SKILL.md                  Full technical documentation, including the version-update troubleshooting guide
tests/test_patch.py       Regression tests for models, reasoning effort, Fast (including 26.1007 custom providers), Browser, CUA, and platform workflows
tests/test_saved_service_tier.py  Saved Fast request auth, capability limits, and Copilot exclusion
tests/test_location_privacy.py  Location defaults, explicit preferences, repeated runs, and fail-closed validation
```

## Usage

### Complete Workflow (Recommended)

All paths are detected automatically; no arguments are required:

```bash
# macOS
python3 patch.py

# Windows PowerShell
python3 patch.py
```

> The script automatically stops running processes, detects the installation, creates a separate copy, extracts and repackages the ASAR, patches the JavaScript, handles platform integrity metadata, and signs the result.

### macOS Paths

By default, the script searches `/Applications` and `~/Applications`, in that order, for `ChatGPT.app` or `Codex.app`. It places the patched copy next to the official app when possible. If that directory is not writable, it automatically uses `~/Applications`. Detection looks for a Codex runtime marker in `Contents/Resources`: the plain `codex`/`codex.exe` file, or `com.openai.codex.manifest` (a file in macOS 26.915 and earlier, a directory from 26.1013).

You can also specify the paths explicitly:

```bash
python3 patch.py \
  --app "$HOME/Applications/Codex.app" \
  --output "$HOME/Applications/ChatGPT-Codex-Patched.app"
```

The script does not modify the official app. On macOS, it updates the ASAR integrity metadata in the copy, removes the quarantine attribute, and applies an ad-hoc signature. When the build embeds Electron's integrity-dictionary digest in the runtime framework (26.1013 and later), the copy's framework is updated with the recomputed digest and re-signed ad-hoc together with its Electron helpers; unrelated frameworks and deeper nested code keep their OpenAI signatures. To allow the ad-hoc signed outer app to load those internal components, library validation is disabled for the outer signature. This weakens protection against dynamic-library injection, so this treatment is used only for the separate copy.

### macOS Browser and Computer Use

With `26.707.31428`, the built-in Browser, Chrome plugin, and Computer Use have been verified to reach the runtime marketplace. The Computer Use helper app retains its OpenAI signature. Its system permission entry appears as **Codex Computer Use** (bundle id `com.openai.sky.CUAService`), not ChatGPT. On first use, grant access in both of these locations:

- `System Settings > Privacy & Security > Accessibility`
- `System Settings > Privacy & Security > Screen & System Audio Recording` (named "Screen Recording" on older macOS versions)

The outermost app in the patched copy has an ad-hoc signature, so macOS cannot resolve a complete responsible-process signature chain for the Browser native pipe. The script downgrades only `missing-code-signing-identity` to an allowed condition. It still rejects `untrusted-code-signing-identity` and a missing socket descriptor, and it restricts the Browser socket to owner-only mode `0600`. This fallback still weakens process-signature isolation among processes running as the same user. The official app remains unchanged and can serve as a fallback for Appshots and the original main-app TCC identity.

### Check or Debug Only the JavaScript Patches

```bash
# macOS (patched copy)
python3 patch.py --assets "/Applications/ChatGPT-Codex-Patched.app/Contents/Resources/app/webview/assets"

# Windows (Store edition, ChatGPT-Codex-Patched)
python3 patch.py --assets "$env:LOCALAPPDATA\Programs\ChatGPT-Codex-Patched\resources\app\webview\assets"
```

`--assets` does not repackage `app.asar` or patch the native `codex.exe`; it is intended for JavaScript version adaptation and testing. To apply the changes to the Owl runtime, including built-in image generation, run the complete `python3 patch.py` workflow.

### Dry Run (No Files Written)

```bash
python3 patch.py --dry-run
```

The complete dry run previews the workflow only; it does not validate patch compatibility because it skips copying and extracting the detected installation. Existing extracted files may belong to an older version. To inspect specific extracted assets without writing files, use `python3 patch.py --assets /path/to/webview/assets --dry-run`.

## Rollback

**macOS:**

```bash
rm -rf "/Applications/ChatGPT-Codex-Patched.app"
# If the copy is in the user directory:
rm -rf "$HOME/Applications/ChatGPT-Codex-Patched.app"
# The legacy-branded copy is named Codex-Patched.app
```

**Windows Store edition:**

```powershell
Remove-Item -Recurse -Force "$env:LOCALAPPDATA\Programs\ChatGPT-Codex-Patched"
# The original Store edition is unchanged and can be launched directly from the Start menu
```

The Store build validates an ASAR header hash embedded in `ChatGPT.exe`. The
workflow updates that hash only in the copied executable after repacking and
gives the patched desktop shortcut a separate `--user-data-dir`, so an already
running official instance cannot claim the patched copy's single-instance lock.
Because the copied executable is modified, Windows may report its vendor
signature as invalid; the official Store installation keeps its original
signature and remains untouched. The Windows 26.1021/26.1023/26.1029/26.1103/26.1106 image-generation patch
also modifies the copied `resources/codex.exe`, invalidating that copy's
vendor signature.

**Traditional Windows installation:**

```powershell
cd "$env:LOCALAPPDATA\Programs\ChatGPT\resources" # The legacy-branded directory is Codex
Remove-Item -Recurse -Force app
if (Test-Path app.asar.bak) { Copy-Item app.asar.bak app.asar -Force }
if (Test-Path codex.exe.imagegen.bak) { Copy-Item codex.exe.imagegen.bak codex.exe -Force }
```

## config.toml

Keep your existing API provider configuration. The patch does not hard-code model IDs or reasoning effort levels, and it does not depend on the legacy `features.enable_fast` setting. Models and reasoning effort levels come from the live app-server list. Options such as Max and Ultra appear only when the model explicitly declares support for them. Selecting Fast writes the `service_tier` setting used by the current version.

Custom providers with `requires_openai_auth = false` can report no OpenAI account and a null authentication method. The reasoning-effort patch also recognizes this custom-provider state, so Max and Ultra are not hidden merely because there is no OpenAI login. Models that do not declare Ultra support still omit that option.

Fast also recognizes this state: the UI accepts `authMethod == null` only when `requiresAuth === false`, and the request path checks `account == null` together with `requiresOpenaiAuth === false`. Fast still requires a matching entry in the model's `serviceTiers` and server permission from `configRequirements/read`. A model without Fast support, or a server response with `fast_mode: false`, keeps the option unavailable.

### Built-in Image Generation

Windows app `26.1021.31345` bundles Codex CLI `0.164.0-alpha.11.1`, app `26.1023.51421` bundles CLI `0.165.0-alpha.1.2`, Store package `26.1029.7803.0` (app `26.1029.61003`) bundles CLI `0.166.0-alpha.6`, and Store package `26.1103.3517.0` bundles CLI `0.167.0-alpha.3.2`. Their native app-server has two additional authentication gates around the image extension. The `26.1103` binary uses the same two machine-code signatures as `26.1029`, so the script keeps both versions in one coherent support set. The complete workflow validates every original or already-patched signature before writing, prepares the result in a temporary copy, and atomically replaces the executable. Traditional installs retain `codex.exe.imagegen.bak`; Store installs already keep the official package unchanged.

The patch does not invent image support. It retains the `[features] image_generation = true` flag, Free-plan exclusion, provider capability, and model image-input checks. In this version, Responses-compatible custom providers default to the image-generation capability, but they must still implement compatible `/images/generations` and `/images/edits` endpoints. See the [official OpenAI image-generation guide](https://developers.openai.com/api/docs/guides/image-generation).

Native signature matching is currently verified for Windows `26.1021`, `26.1023`, `26.1029`, `26.1103` (CLI `0.167.0-alpha.3.2`), and `26.1106` (CLI `0.167.0-alpha.4.1`); macOS is left unchanged. The complete `26.1103` workflow and a 12-second startup smoke test passed on 2026-11-07. A version drift, missing signature, duplicate signature, or mixed signature set fails closed before ASAR repackaging.

## Version Updates

Store package `26.1106.6628.0` (app `26.1106.51428`) bundles Codex CLI `0.167.0-alpha.4.1`. Its renderer emits seven Fast UI modules, eight service-tier request modules, and seven usage-settings modules. The existing Rspack adapters cover every emitted copy, and both native image-generation signatures remain identical to the shared `26.1029`/`26.1103` set.

Validation on 2026-11-12 passed 52 tests and 794 subtests. The complete `26.1106.6628.0` workflow applied 42 patches, skipped 2 obsolete or natively supported gates, and reported zero failures. A final assets dry run reported 0 applied, 42 skipped, and 0 failed checks.

All 24 changed JavaScript files passed syntax checks and matched their packed ASAR entries; all six native sidecars passed hash checks, and each executable contained its matching ASAR header hash exactly once. The backup matched the official archive, both native gates had the expected original/patched signatures, and the copied CLI reported `0.167.0-alpha.4.1`. Five desktop processes remained alive during a 12-second startup check with a temporary profile, with no fatal, integrity, or syntax errors in the startup log. The official Store package remained unchanged, and no billable model request was sent.

Store package `26.1103.7773.0` keeps Codex CLI `0.167.0-alpha.3.2` and the existing native image-generation signatures. Its location adapter changes the synthetic guest preference from `true` to `false` and maps an absent account preference to `false`. Both Data controls and the location runtime read this preference. Explicit account values, parental controls, browser permissions, and the existing Turn on/Turn off actions keep their original behavior. The script validates every matching account-settings chunk before writing and rejects changed or ambiguous query shapes.

Validation on 2026-11-11 passed 52 tests. The Windows `26.1103.7773.0` rebuild applied the 38 existing patches and the additional location-default patch without failures; a final assets dry run recognized all 39 JavaScript checks as already satisfied. All 22 changed JavaScript files passed syntax checks and matched their packed ASAR contents, all six native sidecars were present, and the rebuilt header hash occurred exactly once in the copied executable. The backup matched the official archive, the native signatures and CLI version checks passed, and six desktop processes remained responsive during a 12-second startup check with a temporary profile. No billable model request was sent.

After ChatGPT Codex is updated, JavaScript filenames (including hash suffixes) and variable names may change. The script includes automatic fallback searches and usually adapts without modification. If a patch fails, see the **Version Update Troubleshooting Guide** in [SKILL.md](SKILL.md).

Windows Store `26.1029.7803.0` switches the renderer from named Vite chunks to roughly nine thousand numeric Rspack chunks under `webview/assets/async`. Store `26.1103.3517.0` continues this layout and adds an anonymous shared service-tier resolver. The adapter detects the layout, scans it once using strong semantic markers, patches every emitted copy of the Fast and usage modules only after the whole group validates, and handles the new double-quoted, constant-left minification form. It also follows the taskbar identity helper after Rspack splits it from the Electron caller, while leaving worker/log-directory decoys untouched.

Windows Store `26.1007.9446.0` returns a `serviceTierAccess` map instead of the older `isServiceTierAllowed` flag, and its request gate returns `null` instead of `false`. The adapter handles both forms and upgrades earlier API-key-only Fast patches to include no-login custom providers. After updating, run the complete `python patch.py` workflow and relaunch `ChatGPT Codex (Patched)`; `--assets` alone leaves the runtime's `app.asar` unchanged.

Validation on 2026-10-15 passed 36 tests and 467 subtests. The full Windows rebuild applied 12 patches with no failures, and archive contents, the embedded ASAR hash, and basic startup checks passed. Executing the patched UI and request functions with local app-server responses confirmed Fast access for the custom-provider state and rejection when the server disables Fast; this check does not measure response speed or send a billable model request.

Windows Store `26.1021.3705.0` (app `26.1021.31345`) also accepts Amazon Bedrock in the Fast UI and request gates. The adapter preserves that branch while adding API keys and eligible no-login providers. A separate `readServiceTier` method reads saved settings for queued turns; its account check is now patched too, while keeping the Copilot exclusion and downstream server/model restrictions.

Validation on 2026-10-26 passed 42 tests. The complete Windows workflow applied 13 patches with no failures; a second inspection recognized all 13 as already applied. All four changed JavaScript files passed Node syntax checks and matched their packed ASAR entries. The executable's embedded header hash matched the rebuilt ASAR, and the original archive matched the backup. Extracted functions from the installed bundle passed 60 auth/server cases and a loading-state check using mocked backend responses. The patched processes started and remained responsive; no billable model request or response-speed measurement was performed.

Validation on 2026-10-28 passed 43 tests. The complete Windows `26.1021.3705.0` workflow applied 15 patches with no failures, including both native image-generation gates; a second native inspection recognized both as already applied. The rebuilt ASAR header hash occurred exactly once in the copied `ChatGPT.exe`, the patched desktop process remained alive during a startup smoke test, and a local mock Responses endpoint captured exactly one non-billable request. Its 10-tool payload contained the `image_gen` namespace with the `imagegen` function. No `/images/*` request or external model request was sent.

Validation on 2026-10-29 passed 43 tests. The complete Windows `26.1023.6621.0` workflow selected the `0.165.0-alpha.1.2` native signature set and applied all 15 patches with no failures. The rebuilt ASAR header hash was updated exactly once, the patched `codex.exe --version` command remained functional, both original native signatures were absent, both patched signatures occurred exactly once, and the desktop shortcut targeted the independent patched copy and user-data directory.

Validation on 2026-11-05 passed 45 tests and 787 subtests. The complete Windows `26.1029.7803.0` workflow detected the numeric Rspack layout, applied 28 patches, skipped 2 obsolete/native-supported gates, and reported zero failures. Packed-ASAR inspection confirmed the Fast, model/reasoning, Browser/CUA, dictation, usage, i18n, and taskbar edits. The copied `ChatGPT.exe` contained the rebuilt ASAR header hash exactly once, while the backup matched the official archive. In the copied `codex.exe`, both original `0.166.0-alpha.6` signatures were absent and both patched signatures occurred exactly once; the official package retained both originals and neither patched form. `codex.exe --version` still succeeded, the desktop shortcut targeted the patched copy and isolated user-data directory, and a 12-second startup smoke test kept the main process and its Codex/Node helpers alive before they were cleanly stopped.

Validation on 2026-11-07 passed 46 tests and 787 subtests. The complete Windows Store `26.1103.3517.0` workflow detected the numeric Rspack layout, applied 38 patches, skipped 2 obsolete/native-supported gates, and reported zero failures. The copied `app.asar.bak` matched the official archive, the rebuilt ASAR header hash occurred exactly once in the copied `ChatGPT.exe`, and the copied `codex.exe` reported `codex-cli 0.167.0-alpha.3.2`; both original native signatures were absent and both patched signatures occurred exactly once. The shortcut targeted the independent patched copy and user-data directory, and a 12-second startup smoke test kept the six new desktop processes alive before they were cleanly stopped. The official Store package remained unchanged; no billable model request was sent.

For Nowledge MCP settings that disappear when a configuration manager rewrites Codex settings, see the [configuration persistence investigation](docs/nowledge-mcp-persistence.md). The application patch itself does not manage MCP credentials.
