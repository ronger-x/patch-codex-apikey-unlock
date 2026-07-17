# ChatGPT Codex - Full Feature Unlock for API Key Mode

This project targets the Codex desktop app whose display name has been changed to **ChatGPT**. It unlocks the model list, reasoning effort levels, service tiers, and other features in API key mode.

It has been verified with Windows Store version `26.707.3748.0` and the macOS app version `26.707.31428` (Apple Silicon). In these versions, the display name and primary entry point have changed to ChatGPT, while installation packages may still use the Codex name. The script supports both the old and new names.

## Unlocked Features

| Feature | Description |
|---------|-------------|
| Latest model list | Shows models returned by app-server that are marked as hidden by default |
| Hidden reasoning effort options | In API key mode, shows every reasoning effort level that the model actually supports but the ChatGPT experiment configuration hides |
| Fast/Speed mode | Removes the `chatgpt` login restriction from both the UI and the actual request path |
| Plugins marketplace | Supports the legacy gate; automatically skips the patch when a newer version natively supports API keys |
| Browser / Chrome | Removes the desktop availability gate caused by missing Statsig context in API key mode while preserving real platform and app-server capability checks |
| Computer Use | Enables desktop availability and the Node runtime; on macOS, uses the separately packaged helper service signed by OpenAI |
| Legacy connector UI gate | Supports only the legacy frontend gate; does not impersonate a ChatGPT session-based connector identity |
| Consistent branding | Forces the relevant check to return `false`, regardless of account type |
| Voice input/dictation | Expands availability to `chatgpt \|\| apikey` |
| Usage/billing settings | Expands availability to `chatgpt \|\| apikey` |
| i18n languages | Bypasses the Statsig experiment gate and forces the feature on |

## Prerequisites

- Node.js (the script pins versions of `@electron/asar` and `@electron/fuses` that remain compatible with older Node.js releases)
- Python 3

## Files

```
patch.py                  Main script: one-command workflow for both ChatGPT and Codex names (macOS / Windows)
SKILL.md                  Full technical documentation, including the version-update troubleshooting guide
tests/test_patch.py       Regression tests for 26.707 models, reasoning effort, Fast, Browser, CUA, and the macOS workflow
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

By default, the script searches `/Applications` and `~/Applications`, in that order, for `ChatGPT.app` or `Codex.app`. It places the patched copy next to the official app when possible. If that directory is not writable, it automatically uses `~/Applications`.

You can also specify the paths explicitly:

```bash
python3 patch.py \
  --app "$HOME/Applications/Codex.app" \
  --output "$HOME/Applications/ChatGPT-Codex-Patched.app"
```

The script does not modify the official app. On macOS, it updates the ASAR integrity metadata in the copy, removes the quarantine attribute, and applies an ad-hoc signature. The officially signed runtime frameworks and helpers remain unchanged. To allow the ad-hoc signed outer app to load those internal components, library validation is disabled for the outer signature. This weakens protection against dynamic-library injection, so this treatment is used only for the separate copy.

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

`--assets` does not repackage `app.asar`; it is intended for version adaptation and testing. To apply the changes to the Owl runtime, run the complete `python3 patch.py` workflow.

### Dry Run (No Files Written)

```bash
python3 patch.py --dry-run
```

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

**Traditional Windows installation:**

```powershell
cd "$env:LOCALAPPDATA\Programs\ChatGPT\resources" # The legacy-branded directory is Codex
Remove-Item -Recurse -Force app
if (Test-Path app.asar.bak) { Copy-Item app.asar.bak app.asar -Force }
```

## config.toml

Keep your existing API provider configuration. The patch does not hard-code model IDs or reasoning effort levels, and it does not depend on the legacy `features.enable_fast` setting. Models and reasoning effort levels come from the live app-server list. Options such as Max and Ultra appear only when the model explicitly declares support for them. Selecting Fast writes the `service_tier` setting used by the current version.

## Version Updates

After ChatGPT Codex is updated, JavaScript filenames (including hash suffixes) and variable names may change. The script includes automatic fallback searches and usually adapts without modification. If a patch fails, see the **Version Update Troubleshooting Guide** in [SKILL.md](SKILL.md).
