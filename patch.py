#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ChatGPT Codex - API key feature unlocker
Cross-platform utility for macOS and Windows with automatic path detection.

Usage:
    python3 patch.py                        # Detect the installation and run all steps
    python3 patch.py --assets /path/assets  # Patch only this JS assets directory
    python3 patch.py --dry-run              # Preview operations without writing files
"""

import argparse
import glob
import hashlib
import os
import plistlib
import re
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

# ================================================================
# Argument parsing
# ================================================================
parser = argparse.ArgumentParser(description="Unlock ChatGPT Codex features for API key mode")
parser.add_argument("--assets", metavar="DIR",
                    help="patch a webview/assets directory without ASAR or fuse steps")
parser.add_argument("--app", metavar="APP",
                    help="macOS: path to the official ChatGPT/Codex .app")
parser.add_argument("--output", metavar="APP",
                    help="macOS: output path for the patched .app copy")
parser.add_argument("--dry-run", action="store_true",
                    help="preview operations without writing files")
args = parser.parse_args()

IS_MACOS   = sys.platform == "darwin"
IS_WINDOWS = sys.platform == "win32"
DRY_RUN    = args.dry_run

WINDOWS_EXE_NAMES = ("ChatGPT.exe", "Codex.exe")
WINDOWS_INSTALL_NAMES = ("ChatGPT", "Codex")
# Current ChatGPT-branded builds still use the OpenAI.Codex package identity.
# Keep the ChatGPT package name as a fallback for future identity changes.
WINDOWS_STORE_PACKAGES = ("OpenAI.Codex", "OpenAI.ChatGPT")
MACOS_APP_PATHS = (
    "/Applications/ChatGPT.app",
    "/Applications/Codex.app",
    os.path.expanduser("~/Applications/ChatGPT.app"),
    os.path.expanduser("~/Applications/Codex.app"),
)
MACOS_EXE_NAMES = ("ChatGPT", "Codex")
ASAR_PACKAGE = "@electron/asar@3.4.1"
FUSES_PACKAGE = "@electron/fuses@1.8.0"

if DRY_RUN:
    print("[DRY-RUN] Preview mode; no files will be modified.\n")


# ================================================================
# Utility: run a subprocess
# ================================================================
def run_cmd(cmd, capture=False):
    """Run a command and return (returncode, output_str)."""
    kw = {}
    if capture:
        kw["capture_output"] = True
        kw["text"] = True
    if IS_WINDOWS:
        # On Windows, use shell=True so .cmd wrappers (npx.cmd, etc.) are found.
        # subprocess.list2cmdline properly quotes arguments with spaces.
        cmd = subprocess.list2cmdline(cmd)
        kw["shell"] = True
    result = subprocess.run(cmd, **kw)
    if not capture:
        return result.returncode, ""
    stdout = result.stdout.strip() if result.stdout else ""
    stderr = result.stderr.strip() if result.stderr else ""
    output = stdout
    if result.returncode != 0 and stderr:
        output = "\n".join(part for part in (stdout, stderr) if part)
    return result.returncode, output


def _first_existing_file(base, names):
    for name in names:
        path = os.path.join(base, name)
        if os.path.isfile(path):
            return path
    return None


def _windows_executable(app_root):
    return _first_existing_file(app_root, WINDOWS_EXE_NAMES)


def _macos_executable(app_root):
    macos_dir = os.path.join(app_root, "Contents", "MacOS")
    info_plist = os.path.join(app_root, "Contents", "Info.plist")
    try:
        with open(info_plist, "rb") as fh:
            executable_name = plistlib.load(fh).get("CFBundleExecutable")
        if isinstance(executable_name, str):
            executable = os.path.join(macos_dir, executable_name)
            if os.path.isfile(executable):
                return executable
    except (OSError, plistlib.InvalidFileException):
        pass
    return _first_existing_file(macos_dir, MACOS_EXE_NAMES)


def _is_codex_resources(resources_dir):
    if not os.path.isfile(os.path.join(resources_dir, "app.asar")):
        return False
    return any(os.path.isfile(os.path.join(resources_dir, marker))
               for marker in ("codex", "codex.exe"))


def _store_app_details(store_root):
    """Read the MSIX manifest and return (app_root, resources, executable)."""
    manifest = os.path.join(store_root, "AppxManifest.xml")
    try:
        tree = ET.parse(manifest)
    except (OSError, ET.ParseError) as exc:
        raise ValueError(f"Unable to read the MSIX manifest: {manifest}") from exc

    for element in tree.getroot().iter():
        if element.tag.rsplit("}", 1)[-1] != "Application":
            continue
        executable_rel = element.attrib.get("Executable")
        if not executable_rel:
            continue
        executable = os.path.join(
            store_root, os.path.normpath(executable_rel.replace("/", os.sep)))
        app_root = os.path.dirname(executable)
        resources = os.path.join(app_root, "resources")
        if os.path.isfile(executable) and _is_codex_resources(resources):
            return app_root, resources, executable
    raise ValueError(f"No application with Codex resources found in MSIX: {manifest}")


# ================================================================
# Step 2: stop the verified ChatGPT Codex application
# ================================================================
def step_kill_codex(exe_path, macos_app_paths=()):
    print("[2] Stopping ChatGPT Codex processes...")
    if DRY_RUN:
        print("    [DRY-RUN] Process shutdown skipped")
        return
    process_name = os.path.basename(exe_path)
    if IS_MACOS:
        run_cmd(["pkill", "-x", process_name])
        for app_path in macos_app_paths:
            contents_pattern = re.escape(
                os.path.join(os.path.abspath(app_path), "Contents") + os.sep)
            run_cmd(["pkill", "-f", contents_pattern])
    elif IS_WINDOWS:
        run_cmd(["taskkill", "/F", "/IM", process_name], capture=True)
    time.sleep(1)


# ================================================================
# Step 1: locate and validate the installation
# ================================================================
def step_detect():
    """
    Return (source_root, resources_dir, exe_path, is_store).
    source_root: installation root (read-only MSIX directory for Store builds)
    resources_dir: writable resources directory containing app.asar
    exe_path: executable path in a writable location
    is_store: whether this is a Store build that must first be copied
    """
    print("[1] Locating the ChatGPT Codex installation...")

    if IS_MACOS:
        app_paths = MACOS_APP_PATHS
        if args.app:
            app_paths = (os.path.abspath(os.path.expanduser(args.app)),)
        for app in app_paths:
            resources = os.path.join(app, "Contents", "Resources")
            exe = _macos_executable(app) if os.path.isdir(app) else None
            if exe is None or not _is_codex_resources(resources):
                continue
            print(f"  Found macOS installation: {app} ({os.path.basename(exe)})")
            return app, resources, exe, False
        searched = ", ".join(app_paths)
        _die(f"No valid macOS ChatGPT/Codex app found. Checked: {searched}")

    if IS_WINDOWS:
        local = os.environ.get("LOCALAPPDATA", "")
        if not local:
            _die("The LOCALAPPDATA environment variable is not set.")

        # -- Traditional installation ---------------------------------
        for install_name in WINDOWS_INSTALL_NAMES:
            trad_root = os.path.join(local, "Programs", install_name)
            trad_res = os.path.join(trad_root, "resources")
            trad_exe = _windows_executable(trad_root)
            if (trad_exe is not None and _is_codex_resources(trad_res)):
                print(f"  Found traditional installation: {trad_root} ({os.path.basename(trad_exe)})")
                return trad_root, trad_res, trad_exe, False

        # -- Microsoft Store build (MSIX) ------------------------------
        for package_name in WINDOWS_STORE_PACKAGES:
            rc, store_root = run_cmd(
                ["powershell", "-NoProfile", "-Command",
                 f"Get-AppxPackage -Name '{package_name}' | "
                 "Sort-Object Version -Descending | Select-Object -First 1 "
                 "-ExpandProperty InstallLocation"],
                capture=True
            )
            if rc == 0 and store_root and os.path.isdir(store_root):
                try:
                    _, resources, exe = _store_app_details(store_root)
                except ValueError:
                    continue
                print(f"  Found Store installation (MSIX): {store_root} [{package_name}]")
                return store_root, resources, exe, True

    _die("ChatGPT Codex is not installed as a Store or traditional build.")


def _die(msg):
    print(f"[ERROR] {msg}")
    sys.exit(1)


def _kill_windows_processes_under(root):
    """Force-stop processes whose executable is inside root."""
    ps = (
        "$ErrorActionPreference='Stop'\n"
        "$trimChars=[char[]]'\\/'\n"
        "$root=[IO.Path]::GetFullPath($env:CODEX_PATCH_PROCESS_ROOT)"
        ".TrimEnd($trimChars)\n"
        "$volumeRoot=[IO.Path]::GetPathRoot($root).TrimEnd($trimChars)\n"
        "if([string]::IsNullOrWhiteSpace($root) -or $root -eq $volumeRoot){"
        "throw 'Refusing to scan a filesystem root'}\n"
        "$prefix=$root+[IO.Path]::DirectorySeparatorChar\n"
        "for($attempt=0;$attempt -lt 4;$attempt++){\n"
        "  $processes=@(Get-CimInstance -ClassName Win32_Process | "
        "Where-Object {$_.ExecutablePath -and $_.ExecutablePath.StartsWith("
        "$prefix,[StringComparison]::OrdinalIgnoreCase)})\n"
        "  if($processes.Count -eq 0){exit 0}\n"
        "  if($attempt -eq 3){throw 'Processes are still running'}\n"
        "  foreach($process in $processes){\n"
        "    $targetId=[int]$process.ProcessId\n"
        "    $current=Get-CimInstance -ClassName Win32_Process "
        "-Filter (\"ProcessId = $targetId\") -ErrorAction SilentlyContinue\n"
        "    if($null -ne $current -and $current.ExecutablePath -and "
        "$current.ExecutablePath.StartsWith("
        "$prefix,[StringComparison]::OrdinalIgnoreCase)){\n"
        "      Stop-Process -Id $targetId -Force -ErrorAction SilentlyContinue\n"
        "    }\n"
        "  }\n"
        "  Start-Sleep -Milliseconds 200\n"
        "}\n"
    )
    powershell = os.path.join(
        os.environ.get("SystemRoot", r"C:\Windows"),
        "System32", "WindowsPowerShell", "v1.0", "powershell.exe",
    )
    env = os.environ.copy()
    env["CODEX_PATCH_PROCESS_ROOT"] = os.path.abspath(root)
    # Windows PowerShell 5.1 reads `-Command -` interactively and does not
    # execute a multi-line compound statement at EOF. A complete single-line
    # statement keeps stdin/env transport safe without shell quoting.
    ps = ps.replace("\n", ";")
    try:
        result = subprocess.run(
            [
                powershell, "-NoLogo", "-NoProfile", "-NonInteractive",
                "-Command", "-",
            ],
            input=ps,
            env=env,
            capture_output=True,
            text=True,
            errors="replace",
            shell=False,
        )
    except OSError:
        return False
    return result.returncode == 0


def _rmtree_clear_readonly(function, path, exc_info):
    """shutil.rmtree callback for read-only files copied from WindowsApps."""
    error = exc_info[1]
    if not isinstance(error, PermissionError):
        raise error.with_traceback(exc_info[2])
    os.chmod(path, stat.S_IWRITE)
    function(path)


def _remove_windows_store_copy(path, attempts=5, retry_delay=0.5):
    """Stop runtimes from an old Store copy and remove it with short retries."""
    _kill_windows_processes_under(path)
    for attempt in range(attempts):
        try:
            shutil.rmtree(path, onerror=_rmtree_clear_readonly)
            return
        except FileNotFoundError:
            return
        except OSError:
            if attempt == attempts - 1:
                raise
            _kill_windows_processes_under(path)
            time.sleep(retry_delay * (attempt + 1))


# ================================================================
# Step 3 (Store builds only): copy to a writable directory
# ================================================================
def step_copy_store(store_root):
    """
    Copy the Store app directory to a writable location with
    robocopy /COPY:DAT. Return (patch_root, resources_dir, exe_path).
    """
    local = os.environ["LOCALAPPDATA"]
    try:
        src, _, source_exe = _store_app_details(store_root)
    except ValueError as exc:
        _die(str(exc))

    exe_name = os.path.basename(source_exe)
    patched_dir_name = (
        "ChatGPT-Codex-Patched"
        if exe_name.lower() == "chatgpt.exe"
        else "Codex-Patched"
    )
    patch_root = os.path.join(local, "Programs", patched_dir_name)
    resources  = os.path.join(patch_root, "resources")
    exe        = os.path.join(patch_root, exe_name)

    print(f"[3] Copying the app directory (about 300 MB)...")
    print(f"    {src}")
    print(f"    -> {patch_root}")

    if DRY_RUN:
        print("    [DRY-RUN] Copy skipped")
        return patch_root, resources, exe

    if os.path.exists(patch_root):
        print("    Stopping old patched-copy processes and removing the directory...")
        try:
            _remove_windows_store_copy(patch_root)
        except OSError as exc:
            _die(
                f"Unable to remove the old patched directory: {patch_root}\n"
                "Close ChatGPT/Codex and Computer Use processes started from "
                "that directory. If they run as administrator, rerun this "
                f"script as administrator.\nOriginal error: {exc}"
            )
    os.makedirs(patch_root, exist_ok=True)

    # /COPY:DAT copies data, attributes, and timestamps while omitting EFS
    # encryption attributes that cannot be copied from WindowsApps.
    # /NP suppresses noisy percentages but retains file and directory listings.
    print("    Copying; this may take 1-2 minutes...")
    rc, _ = run_cmd(
        ["robocopy", src, patch_root,
         "/E", "/COPY:DAT", "/NP", "/NDL", "/NJH", "/NJS"]
    )
    if rc >= 8:
        _die(f"robocopy failed (exit {rc}); run this script as administrator.")

    print("    Copy complete.")
    return patch_root, resources, exe


# ================================================================
# Step 4: back up and extract app.asar
# ================================================================
# Codex uses OpenAI's custom "owl" Electron runtime, which loads only from
# resources/app.asar and does not fall back to an app/ directory. It also does
# not expose the standard fuse wire needed to disable OnlyLoadAppFromAsar with
# @electron/fuses. The archive must therefore be extracted, patched, and packed.
def step_extract_asar(resources_dir):
    print("[4] Extracting app.asar...")

    asar     = os.path.join(resources_dir, "app.asar")
    asar_bak = os.path.join(resources_dir, "app.asar.bak")
    app_dir  = os.path.join(resources_dir, "app")

    if DRY_RUN:
        print("    [DRY-RUN] ASAR validation and extraction skipped")
        return

    if not os.path.isfile(asar) and not os.path.isfile(asar_bak):
        _die(f"app.asar not found: {asar}")

    # Back up the original ASAR on the first run.
    if not os.path.isfile(asar_bak):
        shutil.copy2(asar, asar_bak)
        print("    Backed up app.asar -> app.asar.bak")

    # Extract from app.asar so electron/asar can merge native modules from the
    # matching app.asar.unpacked sidecar. Extracting app.asar.bak would look for
    # a mismatched sidecar name and fail with ENOENT. The patches are idempotent.
    if os.path.isdir(app_dir):
        shutil.rmtree(app_dir)

    rc, _ = run_cmd(["npx", "--yes", ASAR_PACKAGE, "e", asar, app_dir])
    if rc != 0:
        _die("ASAR extraction failed; confirm that Node.js and npx are available.")
    print("    Extracted app.asar to app/.")


# ================================================================
# Step 4.5: repack app/ into app.asar
# ================================================================
def step_repack_asar(resources_dir):
    print("\n[5.5] Repacking app/ -> app.asar...")

    asar      = os.path.join(resources_dir, "app.asar")
    app_dir   = os.path.join(resources_dir, "app")
    unpacked  = os.path.join(resources_dir, "app.asar.unpacked")

    if DRY_RUN:
        print("    [DRY-RUN] Repack skipped")
        return

    if not os.path.isdir(app_dir):
        _die(f"Cannot pack because the app/ directory is missing: {app_dir}")

    # Remove stale unpacked files.
    if os.path.isdir(unpacked):
        shutil.rmtree(unpacked)

    # Native .node modules and node-pty/better-sqlite3 must remain unpacked so
    # Electron can load them with dlopen.
    rc, _ = run_cmd([
        "npx", "--yes", ASAR_PACKAGE, "pack", app_dir, asar,
        "--unpack-dir", "{**/node_modules/node-pty,**/node_modules/better-sqlite3}",
        "--unpack", "**/*.node",
    ])
    if rc != 0:
        _die("ASAR packing failed.")

    # Remove the legacy app.asar1 artifact left by older versions.
    asar1 = os.path.join(resources_dir, "app.asar1")
    if os.path.isfile(asar1):
        os.remove(asar1)

    print("    Repack complete; patches are in app.asar and native modules are unpacked.")


# ================================================================
# Step 5: JavaScript patches
# ================================================================
results = {"applied": [], "skipped": [], "failed": []}


def _find(base, pattern):
    return glob.glob(os.path.join(base, pattern))


def apply_patch(fp, name, find_str, replace_str, regex=None, replace_fn=None, skip_regex=None):
    with open(fp, encoding="utf-8") as f:
        content = f.read()
    bn = os.path.basename(fp)

    # A custom skip_regex takes precedence when detecting an existing patch.
    if skip_regex and re.search(skip_regex, content):
        results["skipped"].append(f"{bn}: {name}")
        print(f"    [SKIP] {name}")
        return

    # Fall back to replace_str for backward-compatible patch detection.
    if replace_str and replace_str in content:
        results["skipped"].append(f"{bn}: {name}")
        print(f"    [SKIP] {name}")
        return

    if find_str and find_str in content:
        if not DRY_RUN:
            with open(fp, "w", encoding="utf-8") as f:
                f.write(content.replace(find_str, replace_str, 1))
        results["applied"].append(f"{bn}: {name}")
        print(f"    [OK]   {name}")
        return

    if regex and replace_fn:
        m = re.search(regex, content)
        if m:
            old, new = m.group(0), replace_fn(m)
            if old != new:
                if not DRY_RUN:
                    with open(fp, "w", encoding="utf-8") as f:
                        f.write(content.replace(old, new, 1))
                results["applied"].append(f"{bn}: {name} (regex)")
                print(f"    [OK]   {name} (regex)")
                return

    results["failed"].append(f"{bn}: {name}")
    print(f"    [FAIL] {name}")


def mark_satisfied(fp, name, reason):
    bn = os.path.basename(fp)
    results["skipped"].append(f"{bn}: {name} ({reason})")
    print(f"    [SKIP] {name} ({reason})")


def mark_missing(name, reason):
    results["failed"].append(f"{name}: {reason}")
    print(f"    [FAIL] {name} ({reason})")


def _single_patch_target(files, name, required=True):
    files = sorted(set(files))
    if len(files) == 1:
        return files[0]
    if len(files) == 0:
        if required:
            mark_missing(name, "Target file not found")
        return None
    mark_missing(name, f"Found {len(files)} candidates; refusing an ambiguous edit")
    return None


def _function_spans(content):
    """Yield minified named-function spans without attempting to parse JS."""
    identifier = r'[a-zA-Z_$][a-zA-Z0-9_$]*'
    matches = list(re.finditer(
        rf'function[ \t]+{identifier}[ \t]*\(', content))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        yield match.start(), end


def _statsig_gate_assignment(function_body, feature_name, availability_field):
    """Find the Statsig assignment consumed by one desktop availability check."""
    identifier = r'[a-zA-Z_$][a-zA-Z0-9_$]*'
    feature_marker = f"featureName:`{feature_name}`"
    if feature_marker not in function_body:
        return None

    assignment_pattern = re.compile(
        rf'(?P<gate>{identifier})=(?P<forced>true\|\|)?'
        rf'(?P<hook>{identifier})\(`(?P<gate_id>[0-9]+)`\)'
    )
    candidates = []
    for assignment in assignment_pattern.finditer(function_body):
        gate = assignment.group("gate")
        if re.search(
                rf'{re.escape(availability_field)}[ \t]*:[ \t]*'
                rf'{re.escape(gate)}(?=[,}}])',
                function_body[assignment.end():]):
            candidates.append(assignment)
    return candidates


def _react_auth_context(content):
    """Find the renderer auth context already used by the target chunk."""
    identifier = r'[a-zA-Z_$][a-zA-Z0-9_$]*'
    pattern = re.compile(
        rf'\(0,(?P<react>{identifier})\.useContext\)\('
        rf'(?P<context>{identifier})\)\?\.authMethod===`chatgpt`'
    )
    candidates = {
        (match.group("react"), match.group("context"))
        for match in pattern.finditer(content)
    }
    return next(iter(candidates)) if len(candidates) == 1 else None


def _auth_method_hook(content):
    """Find the existing hook that returns the renderer authentication state."""
    identifier = r'[a-zA-Z_$][a-zA-Z0-9_$]*'
    pattern = re.compile(
        rf'\{{[^{{}}]*authMethod(?:[ \t]*:[ \t]*{identifier})?[^{{}}]*\}}'
        rf'[ \t]*=[ \t]*(?P<hook>{identifier})\(\)'
    )
    candidates = {match.group("hook") for match in pattern.finditer(content)}
    return next(iter(candidates)) if len(candidates) == 1 else None


def apply_browser_computer_use_gate_patch(fp):
    """Enable API-key desktop features while retaining real capability checks."""
    with open(fp, encoding="utf-8") as fh:
        content = fh.read()
    bn = os.path.basename(fp)
    gates = (
        ("browser_use", "isBrowserAgentGateEnabled", "Built-in Browser availability"),
        ("browser_use_external", "isExternalBrowserUseGateEnabled",
         "External Browser availability"),
        ("computer_use", "isComputerUseGateEnabled", "Computer Use availability"),
    )
    auth_context = _react_auth_context(content)
    if auth_context is None:
        mark_missing(
            f"{bn}: Browser / Computer Use API key scope",
            "Ambiguous authentication context",
        )
        return
    react, context = auth_context
    identifier = r'[a-zA-Z_$][a-zA-Z0-9_$]*'
    helper_pattern = re.compile(
        rf'function[ \t]+(?P<helper>{identifier})[ \t]*\(\)[ \t]*\{{'
        rf'[ \t]*return[ \t]*\(0,{re.escape(react)}\.useContext\)\('
        rf'{re.escape(context)}\)\?\.authMethod===`apikey`[ \t]*\}}'
    )
    helper_matches = list(helper_pattern.finditer(content))
    if len(helper_matches) > 1:
        mark_missing(
            f"{bn}: Browser / Computer Use API key scope",
            "Ambiguous authentication hook",
        )
        return
    helper_definition = None
    if helper_matches:
        helper = helper_matches[0].group("helper")
    else:
        used_identifiers = set(re.findall(identifier, content))
        helper = "useCodexApiKeyAuth"
        suffix = 2
        while helper in used_identifiers:
            helper = f"useCodexApiKeyAuth{suffix}"
            suffix += 1
        helper_definition = (
            f"function {helper}(){{return(0,{react}.useContext)({context})"
            "?.authMethod===`apikey`}"
        )

    patched_assignment_pattern = re.compile(
        rf'(?P<gate>{identifier})=\[(?P<hook>{identifier})\('
        rf'`(?P<gate_id>[0-9]+)`\),(?P<helper>{identifier})\(\)\]'
        rf'\.some\(Boolean\)'
    )
    legacy_assignment_pattern = re.compile(
        rf'(?P<gate>{identifier})=\[(?P<hook>{identifier})\('
        rf'`(?P<gate_id>[0-9]+)`\),\(0,{re.escape(react)}\.useContext\)\('
        rf'{re.escape(context)}\)\?\.authMethod===`apikey`\]\.some\(Boolean\)'
    )

    replacements = []
    skipped = []
    validation_failed = False
    for feature_name, availability_field, patch_name in gates:
        candidates = []
        for start, end in _function_spans(content):
            function_body = content[start:end]
            if f"featureName:`{feature_name}`" not in function_body:
                continue
            matches = _statsig_gate_assignment(
                function_body, feature_name, availability_field)
            if matches:
                candidates.extend(
                    (start + match.start(), start + match.end(), match, False)
                    for match in matches
                )
            for match in patched_assignment_pattern.finditer(function_body):
                if match.group("helper") != helper:
                    continue
                gate = match.group("gate")
                if re.search(
                        rf'{re.escape(availability_field)}[ \t]*:[ \t]*'
                        rf'{re.escape(gate)}(?=[,}}])',
                        function_body[match.end():]):
                    candidates.append(
                        (
                            start + match.start(),
                            start + match.end(),
                            match,
                            True,
                        )
                    )
            for match in legacy_assignment_pattern.finditer(function_body):
                gate = match.group("gate")
                if re.search(
                        rf'{re.escape(availability_field)}[ \t]*:[ \t]*'
                        rf'{re.escape(gate)}(?=[,}}])',
                        function_body[match.end():]):
                    candidates.append(
                        (start + match.start(), start + match.end(), match, False)
                    )

        if len(candidates) != 1:
            reason = "Target structure mismatch"
            if len(candidates) > 1:
                reason = f"Found {len(candidates)} targets"
            mark_missing(f"{bn}: {patch_name}", reason)
            validation_failed = True
            continue

        start, end, match, already_patched = candidates[0]
        if already_patched:
            skipped.append(patch_name)
            continue
        replacement = (
            f"{match.group('gate')}=[{match.group('hook')}"
            f"(`{match.group('gate_id')}`),{helper}()].some(Boolean)"
        )
        replacements.append((start, end, replacement, patch_name))

    if validation_failed:
        return
    if not DRY_RUN and replacements:
        for start, end, replacement, _ in sorted(replacements, reverse=True):
            content = content[:start] + replacement + content[end:]
        if helper_definition is not None:
            source_map = re.search(r'(?m)^//# sourceMappingURL=', content)
            insert_at = source_map.start() if source_map else len(content)
            before_helper = content[:insert_at]
            separator = (
                ""
                if not before_helper or before_helper[-1] in ";\r\n"
                else ";\n"
            )
            suffix = "\n" if source_map else ""
            content = (
                before_helper
                + separator
                + helper_definition
                + suffix
                + content[insert_at:]
            )
        with open(fp, "w", encoding="utf-8") as fh:
            fh.write(content)
    for _, _, _, patch_name in replacements:
        results["applied"].append(f"{bn}: {patch_name}")
        print(f"    [OK]   {patch_name}")
    for patch_name in skipped:
        results["skipped"].append(f"{bn}: {patch_name}")
        print(f"    [SKIP] {patch_name}")


def apply_computer_use_node_repl_gate_patch(fp):
    """Enable the current Computer Use runtime variant when CUA is available."""
    with open(fp, encoding="utf-8") as fh:
        content = fh.read()
    bn = os.path.basename(fp)
    identifier = r'[a-zA-Z_$][a-zA-Z0-9_$]*'
    auth_hook = _auth_method_hook(content)
    patch_name = "Computer Use Node runtime"
    if auth_hook is None:
        mark_missing(f"{bn}: {patch_name}", "Ambiguous authentication hook")
        return
    candidates = []
    for function_start, function_end in _function_spans(content):
        function_body = content[function_start:function_end]
        field = re.search(
            rf'computerUseNodeRepl[ \t]*:[ \t]*{identifier}\.available&&'
            rf'(?P<gate>{identifier})(?=[,}}])', function_body)
        if field is None:
            continue
        gate = field.group("gate")
        assignments = list(re.finditer(
            rf'(?P<gate>{re.escape(gate)})=(?P<forced>true\|\|)?'
            rf'(?P<hook>{identifier})\(`(?P<gate_id>[0-9]+)`\)',
            function_body[:field.start()],
        ))
        candidates.extend(
            (function_start + match.start(), function_start + match.end(), match, False)
            for match in assignments
        )
        patched_pattern = re.compile(
            rf'(?P<gate>{re.escape(gate)})=\[(?P<hook>{identifier})\('
            rf'`(?P<gate_id>[0-9]+)`\),{re.escape(auth_hook)}\(\)'
            r'\?\.authMethod===`apikey`\]\.some\(Boolean\)'
        )
        candidates.extend(
            (function_start + match.start(), function_start + match.end(), match, True)
            for match in patched_pattern.finditer(function_body[:field.start()])
        )

    if len(candidates) != 1:
        reason = "Target structure mismatch"
        if len(candidates) > 1:
            reason = f"Found {len(candidates)} targets"
        mark_missing(f"{bn}: {patch_name}", reason)
        return

    start, end, match, already_patched = candidates[0]
    if already_patched:
        results["skipped"].append(f"{bn}: {patch_name}")
        print(f"    [SKIP] {patch_name}")
        return

    replacement = (
        f"{match.group('gate')}=[{match.group('hook')}"
        f"(`{match.group('gate_id')}`),{auth_hook}()?.authMethod==="
        "`apikey`].some(Boolean)"
    )
    if not DRY_RUN:
        content = content[:start] + replacement + content[end:]
        with open(fp, "w", encoding="utf-8") as fh:
            fh.write(content)
    results["applied"].append(f"{bn}: {patch_name}")
    print(f"    [OK]   {patch_name}")


def _desktop_main_build(assets):
    normalized = os.path.normpath(assets)
    if (os.path.basename(normalized) != "assets" or
            os.path.basename(os.path.dirname(normalized)) != "webview"):
        return None
    return os.path.join(
        os.path.dirname(os.path.dirname(normalized)), ".vite", "build"
    )


def apply_windows_app_user_model_id_patch(main_build):
    """Give the patched Windows copy a taskbar identity distinct from Store Codex."""
    identifier = r'[a-zA-Z_$][a-zA-Z0-9_$]*'
    original = re.compile(
        rf'case[ \t]+(?P<flavor>{identifier})\.Prod[ \t]*:[ \t]*'
        r'return[ \t]*`com\.openai\.codex`'
    )
    patched = re.compile(
        rf'case[ \t]+{identifier}\.Prod[ \t]*:[ \t]*'
        r'return[ \t]*`com\.openai\.codex\.patched`'
    )
    candidates = []
    feature_present = False
    for fp in _find(main_build, "file-based-logger-*.js"):
        with open(fp, encoding="utf-8") as fh:
            content = fh.read()
        feature_present = (
            feature_present
            or (".Prod" in content and "com.openai.codex" in content)
        )
        if original.search(content) or patched.search(content):
            candidates.append(fp)

    target = _single_patch_target(
        candidates,
        "Windows taskbar identity",
        required=feature_present,
    )
    if target is None:
        return
    apply_patch(
        target,
        "Windows taskbar identity",
        None,
        None,
        original.pattern,
        lambda match: (
            f"case {match.group('flavor')}.Prod:"
            "return`com.openai.codex.patched`"
        ),
        skip_regex=patched.pattern,
    )


def apply_browser_peer_authorization_patch(fp):
    """Apply the narrow peer fallback and make its native pipe owner-only."""
    with open(fp, encoding="utf-8") as fh:
        content = fh.read()
    bn = os.path.basename(fp)
    patch_name = "Browser macOS peer authorization fallback"
    identifier = r'[a-zA-Z_$][a-zA-Z0-9_$]*'
    authorization_pattern = re.compile(
        rf'(?P<socket>{identifier})=>\{{let (?P<fd>{identifier})='
        rf'(?P<fd_fn>{identifier})\((?P=socket)\);return '
        rf'(?P=fd)==null\?\{{authorized:!1,'
        r'reason:`missing-socket-file-descriptor`\}:'
        rf'(?P<addon>{identifier})\.authorizeSocketPeer\('
        rf'(?P=fd),(?P<dev>{identifier})\)\}}'
    )
    patched_authorization_pattern = re.compile(
        rf'(?P<socket>{identifier})=>\{{let (?P<fd>{identifier})='
        rf'(?P<fd_fn>{identifier})\((?P=socket)\),'
        rf'(?P<authorization>{identifier})=(?P=fd)==null\?\{{authorized:!1,'
        r'reason:`missing-socket-file-descriptor`\}:'
        rf'(?P<addon>{identifier})\.authorizeSocketPeer\('
        rf'(?P=fd),(?P<dev>{identifier})\);return!'
        rf'(?P=authorization)\.authorized&&(?P=authorization)\.reason==='
        r'`missing-code-signing-identity`\?\{authorized:!0\}:'
        rf'(?P=authorization)\}}'
    )
    authorization_matches = list(authorization_pattern.finditer(content))
    patched_authorization_matches = list(
        patched_authorization_pattern.finditer(content))
    if len(authorization_matches) + len(patched_authorization_matches) != 1:
        reason = "Target structure mismatch"
        match_count = len(authorization_matches) + len(
            patched_authorization_matches)
        if match_count > 1:
            reason = f"Found {match_count} authorization targets"
        mark_missing(f"{bn}: {patch_name}", reason)
        return
    authorization_is_patched = bool(patched_authorization_matches)

    chmod_source_pattern = re.compile(
        rf'onListening:(?P<arg>{identifier})=>\{{\(0,'
        rf'(?P<fs>{identifier})\.chmodSync\)\((?P=arg),384\),'
        rf'{identifier}\(\)\.info\(`node_repl_host_services_listening`'
    )
    chmod_sources = list(chmod_source_pattern.finditer(content))
    if len(chmod_sources) != 1:
        reason = "Unique chmod dependency not found"
        if len(chmod_sources) > 1:
            reason = f"Found {len(chmod_sources)} chmod dependencies"
        mark_missing(f"{bn}: {patch_name}", reason)
        return
    fs_module = chmod_sources[0].group("fs")

    listener_pattern = re.compile(
        rf'onListening:(?P<arg>{identifier})=>\{{'
        rf'(?P<chmod>\(0,{re.escape(fs_module)}\.chmodSync\)\('
        rf'(?P=arg),384\),)?'
        rf'{identifier}\(\)\.info\(`browser-use native pipe listening`'
    )
    listener_matches = list(listener_pattern.finditer(content))
    if len(listener_matches) != 1:
        reason = "Unique Browser socket listener not found"
        if len(listener_matches) > 1:
            reason = f"Found {len(listener_matches)} Browser socket listeners"
        mark_missing(f"{bn}: {patch_name}", reason)
        return
    listener_match = listener_matches[0]
    listener_is_patched = listener_match.group("chmod") is not None

    replacements = []
    if not authorization_is_patched:
        match = authorization_matches[0]
        authorization = "_peerAuthorization"
        used_identifiers = set(re.findall(identifier, content))
        suffix = 2
        while authorization in used_identifiers:
            authorization = f"_peerAuthorization{suffix}"
            suffix += 1
        replacement = (
            f"{match.group('socket')}=>{{let {match.group('fd')}="
            f"{match.group('fd_fn')}({match.group('socket')}),{authorization}="
            f"{match.group('fd')}==null?{{authorized:!1,"
            "reason:`missing-socket-file-descriptor`}:"
            f"{match.group('addon')}.authorizeSocketPeer("
            f"{match.group('fd')},{match.group('dev')});return!"
            f"{authorization}.authorized&&{authorization}.reason==="
            "`missing-code-signing-identity`?{authorized:!0}:"
            f"{authorization}}}"
        )
        replacements.append((match.start(), match.end(), replacement))
    if not listener_is_patched:
        insert_at = listener_match.start() + listener_match.group(0).index("{") + 1
        chmod = (
            f"(0,{fs_module}.chmodSync)("
            f"{listener_match.group('arg')},384),"
        )
        replacements.append((insert_at, insert_at, chmod))

    if not replacements:
        results["skipped"].append(f"{bn}: {patch_name}")
        print(f"    [SKIP] {patch_name}")
        return
    if not DRY_RUN:
        for start, end, replacement in sorted(replacements, reverse=True):
            content = content[:start] + replacement + content[end:]
        with open(fp, "w", encoding="utf-8") as fh:
            fh.write(content)
    results["applied"].append(f"{bn}: {patch_name}")
    print(f"    [OK]   {patch_name}")


def _native_apikey_plugins_file(assets):
    identifier = r'[a-zA-Z_$][a-zA-Z0-9_$]*'
    pattern = re.compile(
        rf'function[ \t]+{identifier}[ \t]*\([ \t]*'
        rf'(?P<arg>{identifier})[ \t]*\)[ \t]*\{{[ \t]*'
        r'return(?:[ \t]+(?:\([ \t]*)?|\([ \t]*)'
        r'(?P=arg)[ \t]*!==[ \t]*`chatgpt`[ \t]*&&[ \t]*'
        r'(?P=arg)[ \t]*!==[ \t]*`apikey`'
        r'(?=[ \t]*(?:&&|\)|;|\}))'
    )
    candidates = _find(assets, "use-plugins-*.js")
    candidates.extend(glob.glob(os.path.join(assets, "*.js")))
    for fp in sorted(set(candidates)):
        with open(fp, encoding="utf-8") as fh:
            if pattern.search(fh.read()):
                return fp
    return None


def _model_filter_signature(content, bn, patch_name, required_fields):
    identifier = r'[a-zA-Z_$][a-zA-Z0-9_$]*'
    signature_pattern = re.compile(
        rf'function[ \t]+{identifier}[ \t]*\(\{{(?P<fields>[^}}]*)\}}\)[ \t]*\{{'
    )
    target_signatures = []
    for signature_match in signature_pattern.finditer(content):
        fields = signature_match.group("fields")
        aliases = {}
        for field in required_fields:
            field_match = re.search(
                rf'(?:^|,)[ \t]*{re.escape(field)}'
                rf'(?::(?P<alias>{identifier}))?[ \t]*(?=,|$)',
                fields,
            )
            if field_match is None:
                break
            aliases[field] = field_match.group("alias") or field
        else:
            target_signatures.append((signature_match, aliases))

    if len(target_signatures) != 1:
        reason = "Target structure mismatch"
        if len(target_signatures) > 1:
            reason = f"Found {len(target_signatures)} target functions"
        mark_missing(f"{bn}: {patch_name}", reason)
        return None
    return target_signatures[0]


def apply_model_filter_patch(fp):
    with open(fp, encoding="utf-8") as fh:
        content = fh.read()
    bn = os.path.basename(fp)

    target = _model_filter_signature(
        content,
        bn,
        "Hidden model list unlock",
        ("authMethod", "useHiddenModels"),
    )
    if target is None:
        return

    signature_match, aliases = target
    auth = aliases["authMethod"]
    identifier = r'[a-zA-Z_$][a-zA-Z0-9_$]*'
    next_function = re.search(
        rf'function[ \t]+{identifier}[ \t]*\(', content[signature_match.end():]
    )
    function_end = (
        signature_match.end() + next_function.start()
        if next_function is not None
        else len(content)
    )
    function_body = content[signature_match.end():function_end]
    visibility_pattern = (
        rf'(?P<mode>{identifier})\?(?P<allowed>{identifier})\.has\('
        rf'(?P<model>{identifier})\.model\):!(?P=model)\.hidden'
    )
    condition_pattern = re.compile(visibility_pattern)
    patched_pattern = re.compile(
        rf'{re.escape(auth)}===`apikey`\|\|\((?P<visibility>'
        rf'{visibility_pattern})\)'
    )
    condition_matches = list(condition_pattern.finditer(function_body))
    patched_matches = list(patched_pattern.finditer(function_body))
    if len(patched_matches) == 1 and len(condition_matches) == 1:
        results["skipped"].append(f"{bn}: Hidden model list unlock")
        print("    [SKIP] Hidden model list unlock")
        return
    if patched_matches or len(condition_matches) != 1:
        reason = "Target structure mismatch"
        if len(condition_matches) > 1:
            reason = f"Found {len(condition_matches)} model visibility conditions"
        mark_missing(
            f"{bn}: Hidden model list unlock",
            reason,
        )
        return

    condition_match = condition_matches[0]
    patched = f"{auth}===`apikey`||({condition_match.group(0)})"
    if not DRY_RUN:
        start = signature_match.end() + condition_match.start()
        end = signature_match.end() + condition_match.end()
        content = content[:start] + patched + content[end:]
        with open(fp, "w", encoding="utf-8") as fh:
            fh.write(content)
    results["applied"].append(f"{bn}: Hidden model list unlock (regex)")
    print("    [OK]   Hidden model list unlock (regex)")


def _js_block_end(content, opening_brace):
    """Return the offset after a JS block, ignoring quoted strings and comments."""
    depth = 0
    quote = None
    escaped = False
    line_comment = False
    block_comment = False
    i = opening_brace
    while i < len(content):
        char = content[i]
        next_char = content[i + 1] if i + 1 < len(content) else ""
        if line_comment:
            if char in "\r\n":
                line_comment = False
        elif block_comment:
            if char == "*" and next_char == "/":
                block_comment = False
                i += 1
        elif quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif char == "/" and next_char == "/":
            line_comment = True
            i += 1
        elif char == "/" and next_char == "*":
            block_comment = True
            i += 1
        elif char in "'\"`":
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return None


def apply_reasoning_effort_filter_patch(fp, validate_only=False):
    with open(fp, encoding="utf-8") as fh:
        content = fh.read()
    bn = os.path.basename(fp)
    patch_name = f"{bn}: Reasoning effort list unlock"
    identifier = r'[a-zA-Z_$][a-zA-Z0-9_$]*'

    signature_pattern = re.compile(
        rf'function[ \t]+{identifier}[ \t]*\([ \t]*\{{'
        rf'(?P<fields>[^}}]*)\}}[ \t]*\)[ \t]*\{{'
    )

    def field_alias(fields, field):
        match = re.search(
            rf'(?:^|,)[ \t]*{re.escape(field)}'
            rf'(?::(?P<alias>{identifier}))?[ \t]*(?=,|$)',
            fields,
        )
        return (match.group("alias") or field) if match is not None else None

    target_signatures = []
    required_fields = (
        "authMethod",
        "enabledReasoningEfforts",
        "includeUltraReasoningEffort",
        "models",
        "useHiddenModels",
    )
    for match in signature_pattern.finditer(content):
        aliases = {
            field: field_alias(match.group("fields"), field)
            for field in required_fields
        }
        if all(aliases.values()):
            target_signatures.append((match, aliases))
    if len(target_signatures) != 1:
        mark_missing(patch_name, "Target structure mismatch")
        return False

    signature_match, aliases = target_signatures[0]
    scope_start = signature_match.start()
    scope_end = _js_block_end(content, signature_match.end() - 1)
    if scope_end is None:
        mark_missing(patch_name, "Target structure mismatch")
        return False
    scope = content[scope_start:scope_end]
    auth = aliases["authMethod"]
    enabled = aliases["enabledReasoningEfforts"]
    ultra_gate = aliases["includeUltraReasoningEffort"]

    ultra_original = re.compile(
        rf'(?P<target>{identifier})={re.escape(ultra_gate)}\?'
        rf'(?P<model>{identifier})\.supportedReasoningEfforts:'
        r'(?P=model)\.supportedReasoningEfforts\.filter\(\(\{'
        rf'reasoningEffort:(?P<effort>{identifier})\}}\)=>'
        r'(?P=effort)!==`ultra`\)'
    )
    ultra_patched = re.compile(
        rf'(?P<target>{identifier})=\({re.escape(auth)}===`apikey`\|\|'
        rf'{re.escape(ultra_gate)}\)\?(?P<model>{identifier})\.'
        r'supportedReasoningEfforts:(?P=model)\.supportedReasoningEfforts\.'
        r'filter\(\(\{reasoningEffort:(?P<effort>'
        rf'{identifier})\}}\)=>(?P=effort)!==`ultra`\)'
    )
    enabled_original = re.compile(
        rf'(?P<prefix>\({re.escape(auth)}===`copilot`\?\[.*?\]:'
        rf'(?P<efforts>{identifier})\))\.filter\(\(\{{'
        r'reasoningEffort:(?P<effort>'
        rf'{identifier})\}}\)=>(?P<validator>{identifier})\('
        rf'(?P=effort)\)&&{re.escape(enabled)}\.has\((?P=effort)\)\)'
    )
    enabled_patched = re.compile(
        rf'(?P<prefix>\({re.escape(auth)}===`copilot`\?\[.*?\]:'
        rf'(?P<efforts>{identifier})\))\.filter\(\(\{{'
        rf'reasoningEffort:(?P<effort>{identifier})\}}\)=>'
        rf'(?P<validator>{identifier})\((?P=effort)\)&&\('
        rf'{re.escape(auth)}===`apikey`\|\|{re.escape(enabled)}\.'
        r'has\((?P=effort)\)\)\)'
    )

    ultra_original_matches = list(ultra_original.finditer(scope))
    ultra_patched_matches = list(ultra_patched.finditer(scope))
    enabled_original_matches = list(enabled_original.finditer(scope))
    enabled_patched_matches = list(enabled_patched.finditer(scope))
    if (len(ultra_original_matches) + len(ultra_patched_matches) != 1 or
            len(enabled_original_matches) + len(enabled_patched_matches) != 1):
        mark_missing(patch_name, "Target structure mismatch")
        return False

    ultra_match = (ultra_original_matches or ultra_patched_matches)[0]
    enabled_match = (enabled_original_matches or enabled_patched_matches)[0]
    if enabled_match.group("efforts") != ultra_match.group("target"):
        mark_missing(patch_name, "Target structure mismatch")
        return False
    if enabled_patched_matches:
        shadowed_names = {
            auth,
            enabled,
            enabled_match.group("validator"),
        }
        if enabled_match.group("effort") in shadowed_names:
            mark_missing(patch_name, "Target structure mismatch")
            return False
    if validate_only:
        return True
    if ultra_patched_matches and enabled_patched_matches:
        results["skipped"].append(patch_name)
        print("    [SKIP] Reasoning effort list unlock")
        return True

    edits = []
    if ultra_original_matches:
        model = ultra_match.group("model")
        effort = ultra_match.group("effort")
        replacement = (
            f"{ultra_match.group('target')}=({auth}===`apikey`||{ultra_gate})?"
            f"{model}.supportedReasoningEfforts:"
            f"{model}.supportedReasoningEfforts.filter("
            f"({{reasoningEffort:{effort}}})=>{effort}!==`ultra`)"
        )
        edits.append((ultra_match.start(), ultra_match.end(), replacement))
    if enabled_original_matches:
        used_identifiers = set(re.findall(identifier, scope))
        patched_effort = "__codexReasoningEffort"
        suffix = 2
        while patched_effort in used_identifiers:
            patched_effort = f"__codexReasoningEffort{suffix}"
            suffix += 1
        replacement = (
            f"{enabled_match.group('prefix')}.filter("
            f"({{reasoningEffort:{patched_effort}}})=>"
            f"{enabled_match.group('validator')}({patched_effort})&&"
            f"({auth}===`apikey`||{enabled}.has({patched_effort})))"
        )
        edits.append((enabled_match.start(), enabled_match.end(), replacement))

    patched_scope = scope
    for start, end, replacement in sorted(edits, reverse=True):
        patched_scope = patched_scope[:start] + replacement + patched_scope[end:]
    if (ultra_original.search(patched_scope) is not None or
            enabled_original.search(patched_scope) is not None or
            len(list(ultra_patched.finditer(patched_scope))) != 1 or
            len(list(enabled_patched.finditer(patched_scope))) != 1):
        mark_missing(patch_name, "Post-patch validation failed")
        return False

    patched_content = content[:scope_start] + patched_scope + content[scope_end:]
    if not DRY_RUN:
        with open(fp, "w", encoding="utf-8") as fh:
            fh.write(patched_content)
    results["applied"].append(f"{patch_name} (regex)")
    print("    [OK]   Reasoning effort list unlock (regex)")
    return True


def step_patch_js(assets):
    print(f"[5] Applying JavaScript patches...")
    print(f"    {assets}\n")
    main_build = _desktop_main_build(assets)

    # -- Module 1: Fast mode and service tier (2 patches) ---------------
    # Current builds (26.602+) keep this logic in
    # use-service-tier-settings-*.js. The a=i?.authMethod===`chatgpt`
    # expression gates isServiceTierAllowed. Add apikey to the accepted
    # methods without changing the behavior for other authentication modes.
    # Older builds use use-is-fast-mode-enabled-*.js with canUseFastMode.
    print("  [Module 1] Fast mode and service tier")
    files = _find(assets, "use-service-tier-settings-*.js")
    if not files:
        files = _find(assets, "use-is-fast-mode-enabled-*.js")
    if not files:
        for f in glob.glob(os.path.join(assets, "*.js")):
            with open(f, encoding="utf-8") as fh:
                c = fh.read()
            if "isServiceTierAllowed" in c and "authMethod===`chatgpt`" in c:
                files = [f]; break
    fast_ui_fp = _single_patch_target(files, "Service tier authorization gate")
    if fast_ui_fp is not None:
        # Also recognize and normalize the older true|| patch form.
        apply_patch(fast_ui_fp, "Service tier authorization gate",
            None, None,
            r'([a-zA-Z_$]+)=(?:true\|\|)?([a-zA-Z_$]+)\?\.'
            r'authMethod===`chatgpt`,([a-zA-Z_$]+)=\2\?\.'
            r'authMethod\?\?null',
            lambda m: (
                f"{m.group(1)}={m.group(2)}?.authMethod===`apikey`||"
                f"{m.group(2)}?.authMethod===`chatgpt`,"
                f"{m.group(3)}={m.group(2)}?.authMethod??null"
            ),
            skip_regex=(
                r'=[a-zA-Z_$]+\?\.authMethod===`apikey`\|\|'
                r'[a-zA-Z_$]+\?\.authMethod===`chatgpt`,'
                r'[a-zA-Z_$]+=[a-zA-Z_$]+\?\.authMethod\?\?null'
            ))

    # In 26.707+, request construction applies a second chatgpt-only check.
    request_tier_files = _find(assets, "read-service-tier-for-request-*.js")
    if not request_tier_files:
        for f in glob.glob(os.path.join(assets, "*.js")):
            with open(f, encoding="utf-8") as fh:
                c = fh.read()
            if "Failed to read service tier for request" in c:
                request_tier_files = [f]
                break
    request_tier_fp = _single_patch_target(
        request_tier_files, "Fast request service tier gate", required=False)
    if request_tier_fp is not None:
        apply_patch(request_tier_fp, "Fast request service tier gate",
            None, None,
            r'if\(([a-zA-Z_$]+)!==`chatgpt`\)return!1;',
            lambda m: f"if({m.group(1)}!==`chatgpt`&&{m.group(1)}!==`apikey`)return!1;",
            skip_regex=r'if\(([a-zA-Z_$]+)!==`chatgpt`&&\1!==`apikey`\)return!1;')

    # -- Module 2: latest models and reasoning efforts (2 patches) ------
    # API key sessions do not receive the ChatGPT Statsig hidden-model
    # allowlist. list-models-for-host already requests includeHidden=true,
    # so the API key branch may display the models returned by the backend
    # while the explicit ChatGPT account allowlist remains unchanged.
    print("\n  [Module 2] Latest models and reasoning efforts")
    model_filter_files = _find(assets, "model-list-filter-*.js")
    if not model_filter_files:
        for f in glob.glob(os.path.join(assets, "*.js")):
            with open(f, encoding="utf-8") as fh:
                c = fh.read()
            if "useHiddenModels" in c and ".hidden" in c and ".supportedReasoningEfforts" in c:
                model_filter_files = [f]
                break
    if model_filter_files:
        model_filter_fp = _single_patch_target(
            model_filter_files, "Hidden model list unlock")
        if model_filter_fp is not None:
            with open(model_filter_fp, encoding="utf-8") as fh:
                model_filter_content = fh.read()
            has_reasoning_filters = (
                "enabledReasoningEfforts" in model_filter_content or
                "includeUltraReasoningEffort" in model_filter_content
            )
            reasoning_ready = (
                not has_reasoning_filters or
                apply_reasoning_effort_filter_patch(
                    model_filter_fp, validate_only=True)
            )
            if reasoning_ready:
                failed_before_model_patch = len(results["failed"])
                apply_model_filter_patch(model_filter_fp)
                if (has_reasoning_filters and
                        len(results["failed"]) == failed_before_model_patch):
                    apply_reasoning_effort_filter_patch(model_filter_fp)
    else:
        legacy_model_files = []
        legacy_pattern = re.compile(
            r'[a-zA-Z_$]+=[a-zA-Z_$]+\?\.models\.some\('
            r'[a-zA-Z_$]+\)\?\?!1'
        )
        legacy_skip_pattern = re.compile(
            r'[a-zA-Z_$]+=true\|\|\([a-zA-Z_$]+\?\.models\.some\('
            r'[a-zA-Z_$]+\)\?\?!1\)'
        )
        for f in glob.glob(os.path.join(assets, "*.js")):
            with open(f, encoding="utf-8") as fh:
                c = fh.read()
            if legacy_pattern.search(c) or legacy_skip_pattern.search(c):
                legacy_model_files.append(f)
        legacy_model_fp = _single_patch_target(
            legacy_model_files, "Model availability check (legacy)")
        if legacy_model_fp is not None:
            apply_patch(legacy_model_fp, "Model availability check (legacy)",
                None, None,
                r'([a-zA-Z_$]+)=([a-zA-Z_$]+)\?\.models\.some\('
                r'([a-zA-Z_$]+)\)\?\?!1',
                lambda m: (
                    f"{m.group(1)}=true||({m.group(2)}?.models.some("
                    f"{m.group(3)})??!1)"
                ),
                skip_regex=(
                    r'[a-zA-Z_$]+=true\|\|\([a-zA-Z_$]+\?\.models\.some\('
                    r'[a-zA-Z_$]+\)\?\?!1\)'
                ))

    # -- Module 3: i18n (1 patch) --------------------------------------
    # Current builds use the React Compiler form
    # s=a?.get(`enable_i18n`,!1) in app-main. Older builds use
    # r=(0,Q.useMemo)(()=>n?.get(`enable_i18n`,!1),[n]). The old
    # pluginsDisabledTooltip gate was removed; module 4 handles its successor.
    print("\n  [Module 3] i18n")
    i18n_target = re.compile(
        r'[a-zA-Z_$]+=[a-zA-Z_$]+\?\.get\(`enable_i18n`,!1\)'
    )
    i18n_patched = re.compile(
        r'=true\|\|[a-zA-Z_$]+\?\.get\(`enable_i18n`,!1\)'
    )
    files = []
    for fp in glob.glob(os.path.join(assets, "*.js")):
        with open(fp, encoding="utf-8") as fh:
            content = fh.read()
        if i18n_target.search(content) or i18n_patched.search(content):
            files.append(fp)
    i18n_fp = _single_patch_target(
        files, "Force-enable i18n", required=False)
    if i18n_fp is not None:
        apply_patch(i18n_fp, "Force-enable i18n",
            None, None,
            r'([a-zA-Z_$]+)=([a-zA-Z_$]+)\?\.get\(`enable_i18n`,!1\)',
            lambda m: f"{m.group(1)}=true||{m.group(2)}?.get(`enable_i18n`,!1)",
            skip_regex=r'=true\|\|[a-zA-Z_$]+\?\.get\(`enable_i18n`,!1\)')

    # -- Module 4: Browser / Computer Use (5 patches) ------------------
    # API key sessions lack a ChatGPT Statsig user context, so three desktop
    # availability gates return statsig-disabled and the main process removes
    # browser/chrome/computer-use from the bundled marketplace. Combine only
    # API key authentication with the original gates. Platform, WSL,
    # app-server experimental feature, and plugin configuration checks remain.
    print("\n  [Module 4] Browser / Computer Use")
    desktop_gate_files = []
    desktop_gate_feature_present = False
    desktop_gate_markers = (
        "featureName:`browser_use`",
        "isBrowserAgentGateEnabled",
        "featureName:`browser_use_external`",
        "isExternalBrowserUseGateEnabled",
        "featureName:`computer_use`",
        "isComputerUseGateEnabled",
    )
    for fp in glob.glob(os.path.join(assets, "*.js")):
        with open(fp, encoding="utf-8") as fh:
            content = fh.read()
        desktop_gate_feature_present = (
            desktop_gate_feature_present
            or any(marker in content for marker in desktop_gate_markers)
        )
        if all(marker in content for marker in desktop_gate_markers):
            desktop_gate_files.append(fp)
    desktop_gate_fp = _single_patch_target(
        desktop_gate_files,
        "Browser / Computer Use availability",
        required=desktop_gate_feature_present,
    )
    if desktop_gate_fp is not None:
        apply_browser_computer_use_gate_patch(desktop_gate_fp)

    node_runtime_files = []
    node_runtime_feature_present = False
    node_runtime_pattern = re.compile(
        r'computerUseNodeRepl[ \t]*:[ \t]*'
        r'[a-zA-Z_$][a-zA-Z0-9_$]*\.available&&'
        r'[a-zA-Z_$][a-zA-Z0-9_$]*'
    )
    for fp in glob.glob(os.path.join(assets, "*.js")):
        with open(fp, encoding="utf-8") as fh:
            content = fh.read()
        node_runtime_feature_present = (
            node_runtime_feature_present or "computerUseNodeRepl" in content
        )
        if node_runtime_pattern.search(content):
            node_runtime_files.append(fp)
    node_runtime_fp = _single_patch_target(
        node_runtime_files,
        "Computer Use Node runtime",
        required=node_runtime_feature_present,
    )
    if node_runtime_fp is not None:
        apply_computer_use_node_repl_gate_patch(node_runtime_fp)

    # The outer macOS patched app has an ad-hoc signature. The Browser native
    # addon can return missing-code-signing-identity through the responsible
    # process chain even though node, codex sandbox, and node_repl retain OpenAI
    # signatures. Relax only that reason; retain untrusted identity and missing
    # file descriptor rejection paths.
    peer_auth_files = []
    peer_auth_feature_present = False
    if IS_MACOS and main_build and os.path.isdir(main_build):
        for fp in _find(main_build, "main-*.js"):
            with open(fp, encoding="utf-8") as fh:
                content = fh.read()
            peer_auth_feature_present = (
                peer_auth_feature_present
                or "browser-use-peer-authorization.node" in content
            )
            if ("browser-use-peer-authorization.node" in content and
                    "authorizeSocketPeer" in content and
                    "missing-socket-file-descriptor" in content):
                peer_auth_files.append(fp)
    peer_auth_fp = _single_patch_target(
        peer_auth_files,
        "Browser macOS peer authorization fallback",
        required=peer_auth_feature_present,
    )
    if peer_auth_fp is not None:
        apply_browser_peer_authorization_patch(peer_auth_fp)

    # -- Module 5: legacy plugin connector UI gate (1 patch) -----------
    print("\n  [Module 5] Legacy plugin connector UI gate")
    native_plugins_fp = _native_apikey_plugins_file(assets)
    files = _find(assets, "check-plugin-availability-*.js")
    if files:
        for fp in files:
            apply_patch(fp, "Legacy plugin connector UI gate",
                "(i=`connector-unavailable`)", "false&&(i=`connector-unavailable`)",
                r'(?<!&&)\(([a-zA-Z_$])=`connector-unavailable`\)',
                lambda m: f"false&&({m.group(1)}=`connector-unavailable`)",
                skip_regex=r'false&&\([a-zA-Z_$]=`connector-unavailable`\)')
    elif native_plugins_fp is not None:
        mark_satisfied(
            native_plugins_fp,
            "Legacy plugin connector UI gate",
            "Removed from current build",
        )

    # -- Module 6: branding and plugin marketplace gate (1 patch) ------
    # Current builds (26.602+) use function ge(e){return e!==`chatgpt`} in
    # use-plugins-*.js to control both branding and the plugin sidebar. The
    # function and argument identifiers are no longer necessarily the same.
    # Older builds use plugin-auth-*.js or gradient-*.js.
    print("\n  [Module 6] Branding and plugin marketplace gate")
    if native_plugins_fp is not None:
        mark_satisfied(
            native_plugins_fp,
            "Branding/plugin marketplace compatibility",
            "Current build natively supports API keys",
        )
    else:
        files = _find(assets, "use-plugins-*.js")
        if not files:
            files = _find(assets, "plugin-auth-*.js")
        if not files:
            files = _find(assets, "gradient-*.js")
            if files:
                with open(files[0], encoding="utf-8") as fh:
                    if "chatgpt" not in fh.read():
                        files = []
        if not files:
            for f in glob.glob(os.path.join(assets, "*.js")):
                with open(f, encoding="utf-8") as fh:
                    if re.search(r'function [a-zA-Z_$]+\([a-zA-Z_$]+\)\{return [a-zA-Z_$]+!==`chatgpt`\}', fh.read()):
                        files = [f]; break
        for fp in files:
            # function ge(e){return e!==`chatgpt`}  ->  {return false&&e!==`chatgpt`}
            apply_patch(fp, "Branding/plugin compatibility",
                None, None,
                r'function ([a-zA-Z_$]+)\(([a-zA-Z_$]+)\)\{return \2!==`chatgpt`\}',
                lambda m: f"function {m.group(1)}({m.group(2)}){{return false&&{m.group(2)}!==`chatgpt`}}",
                skip_regex=r'function [a-zA-Z_$]+\([a-zA-Z_$]+\)\{return false&&[a-zA-Z_$]+!==`chatgpt`\}')

    # -- Module 7: dictation (1 patch) ---------------------------------
    # Current builds (26.602+) use n&&t.authMethod===`chatgpt` in
    # use-is-dictation-supported-*.js. Older builds use
    # annotation-comment-editor-card-*.js.
    print("\n  [Module 7] Dictation")
    files = _find(assets, "use-is-dictation-supported-*.js")
    if not files:
        files = _find(assets, "annotation-comment-editor-card-*.js")
    if not files:
        # Match only files with the dictation predicate to avoid app-main.
        dictation_original = re.compile(
            r'[a-zA-Z_$]+&&[a-zA-Z_$]+\.authMethod===`chatgpt`'
        )
        dictation_patched = re.compile(
            r'[a-zA-Z_$]+&&\([a-zA-Z_$]+\.authMethod===`chatgpt`\|\|'
            r'[a-zA-Z_$]+\.authMethod===`apikey`\)'
        )
        for f in glob.glob(os.path.join(assets, "*.js")):
            with open(f, encoding="utf-8") as fh:
                c = fh.read()
            if "dictation" in c.lower() and (
                    dictation_original.search(c) or dictation_patched.search(c)):
                files = [f]; break
    for fp in files:
        apply_patch(fp, "Dictation unlock",
            None, None,
            r'([a-zA-Z_$]+)&&([a-zA-Z_$]+)\.authMethod===`chatgpt`(?!\|\|)',
            lambda m: f"{m.group(1)}&&({m.group(2)}.authMethod===`chatgpt`||{m.group(2)}.authMethod===`apikey`)",
            skip_regex=r'authMethod===`chatgpt`\|\|[a-zA-Z_$]+\.authMethod===`apikey`')

    # -- Module 8: usage settings (1 patch) ----------------------------
    print("\n  [Module 8] Usage settings")
    files = _find(assets, "use-usage-settings-access-*.js")
    if not files:
        for f in glob.glob(os.path.join(assets, "*.js")):
            with open(f, encoding="utf-8") as fh:
                if re.search(r'let [a-zA-Z_$]+=[a-zA-Z_$]+===`chatgpt`', fh.read()):
                    files = [f]; break
    for fp in files:
        apply_patch(fp, "Usage settings unlock",
            "let r=e===`chatgpt`", "let r=e===`chatgpt`||e===`apikey`",
            r'let\s+([a-zA-Z_$]+)=([a-zA-Z_$]+)===`chatgpt`(?!\|\|)',
            lambda m: f"let {m.group(1)}={m.group(2)}===`chatgpt`||{m.group(2)}===`apikey`",
            skip_regex=r'let [a-zA-Z_$]+=[a-zA-Z_$]+===`chatgpt`\|\|[a-zA-Z_$]+===`apikey`')

    # When Store/traditional installs and the patched copy share the official
    # AUMID, pinned taskbar entries reopen the Store build. Assign a separate
    # identity only to the Windows copy; leave the macOS bundle unchanged.
    if IS_WINDOWS and main_build and os.path.isdir(main_build):
        print("\n  [Module 9] Windows taskbar identity")
        apply_windows_app_user_model_id_patch(main_build)


def _require_successful_js_patch():
    """Stop a full build before packaging or signing any partial JS result."""
    if results["failed"]:
        _die("JavaScript patch validation failed; packaging and signing stopped.")


# ================================================================
# Step 6: disable Electron fuses
# ================================================================
def step_fuses(exe_path):
    print("\n[6] Disabling Electron fuses...")
    flags = [
        "OnlyLoadAppFromAsar=off",
        "EnableEmbeddedAsarIntegrityValidation=off",
        "GrantFileProtocolExtraPrivileges=off",
        "EnableCookieEncryption=off",
    ]
    if DRY_RUN:
        for flag in flags:
            print(f"    {flag}")
        return

    no_sentinel = False
    for flag in flags:
        rc, out = run_cmd(
            ["npx", "--yes", FUSES_PACKAGE, "write", "--app", exe_path, flag],
            capture=True)
        if rc != 0:
            if "sentinel" in out.lower():
                no_sentinel = True
            print(f"    [SKIP] {flag}")
        else:
            print(f"    {flag}")

    if no_sentinel:
        # OpenAI's owl Electron build does not expose the standard fuse wire,
        # so a missing sentinel is expected. The patches still take effect
        # through the normal app.asar load path without changing any fuse.
        print("    Note: this Electron build does not expose fuse sentinels.")
        print("          The patches are already packed into app.asar.")


# ================================================================
# Step 3 (macOS only): copy the official app to an independent bundle
# ================================================================
# macOS binds TCC permissions such as screen recording, accessibility, and
# automation to the code-signing Designated Requirement rather than bundle ID.
# Ad-hoc signing the official app would make macOS treat it as another app, so
# retain the official app for Appshots and original TCC identity fallback and
# write patches only to an independent copy. Computer Use has a separate helper
# that retains its OpenAI signature and does not depend on the copy's Team ID.


def _macos_patched_path(official_app, requested_output=None):
    source_exe = _macos_executable(official_app)
    if source_exe is None:
        _die(f"ChatGPT/Codex executable not found: {official_app}")
    patched_name = (
        "ChatGPT-Codex-Patched.app"
        if os.path.basename(source_exe) == "ChatGPT"
        else "Codex-Patched.app"
    )
    if requested_output:
        patched_app = os.path.abspath(os.path.expanduser(requested_output))
        if not patched_app.lower().endswith(".app"):
            _die("--output must be a path ending in .app.")
        return patched_app

    source_parent = os.path.dirname(os.path.abspath(official_app))
    if os.access(source_parent, os.W_OK):
        return os.path.join(source_parent, patched_name)
    return os.path.join(os.path.expanduser("~/Applications"), patched_name)


def _validate_macos_copy_paths(official_app, patched_app):
    source_real = os.path.realpath(official_app)
    output_real = os.path.realpath(patched_app)
    try:
        if os.path.exists(patched_app) and os.path.samefile(official_app, patched_app):
            _die("The patched copy cannot have the same path as the official app.")
    except OSError as exc:
        _die(f"Unable to validate macOS app paths: {exc}")

    try:
        common = os.path.commonpath((source_real, output_real))
    except ValueError as exc:
        _die(f"Unable to validate macOS app paths: {exc}")
    if common in (source_real, output_real):
        _die("Official and patched app paths cannot be equal or nested.")


def step_copy_macos(official_app, requested_output=None):
    """
    Copy the official ChatGPT/Codex app to an independent patched bundle,
    retaining attributes, symlinks, and extended attributes. Return
    (patched_app, resources_dir, exe_path).
    """
    source_exe = _macos_executable(official_app)
    if source_exe is None:
        _die(f"ChatGPT/Codex executable not found: {official_app}")
    exe_name = os.path.basename(source_exe)
    patched_app = _macos_patched_path(official_app, requested_output)
    resources   = os.path.join(patched_app, "Contents", "Resources")
    exe         = os.path.join(patched_app, "Contents", "MacOS", exe_name)
    _validate_macos_copy_paths(official_app, patched_app)

    print("[3] Copying the official app to an independent bundle...")
    print(f"    {official_app}")
    print(f"    -> {patched_app}")
    print(f"    (Official app remains unchanged: {official_app})")

    if DRY_RUN:
        print("    [DRY-RUN] Copy skipped")
        return patched_app, resources, exe

    os.makedirs(os.path.dirname(patched_app), exist_ok=True)
    if os.path.exists(patched_app):
        shutil.rmtree(patched_app)

    # Preserve the bundle, symlinks, and extended attributes. Only quarantine
    # is removed during finalization.
    rc, _ = run_cmd(["ditto", official_app, patched_app])
    if rc != 0:
        _die(f"App copy failed; destination is not writable: {os.path.dirname(patched_app)}")

    print("    Copy complete.")
    return patched_app, resources, exe


def _asar_header_hash(asar_path):
    """Return Electron's SHA-256 integrity hash for an ASAR JSON header."""
    try:
        with open(asar_path, "rb") as fh:
            size_pickle = fh.read(8)
            if len(size_pickle) != 8:
                raise ValueError("ASAR size header is truncated")
            header_size = struct.unpack_from("<I", size_pickle, 4)[0]
            header_pickle = fh.read(header_size)
        if len(header_pickle) != header_size or header_size < 8:
            raise ValueError("ASAR JSON header is truncated")
        string_size = struct.unpack_from("<I", header_pickle, 4)[0]
        header = header_pickle[8:8 + string_size]
        if len(header) != string_size:
            raise ValueError("ASAR JSON header has an invalid size")
    except (OSError, struct.error, ValueError) as exc:
        raise ValueError(f"Unable to read ASAR header: {asar_path}") from exc
    return hashlib.sha256(header).hexdigest()


def step_update_macos_asar_integrity(app_path):
    print("\n[6] Updating macOS ASAR integrity metadata...")
    if DRY_RUN:
        print("    [DRY-RUN] Integrity metadata update skipped")
        return

    info_plist = os.path.join(app_path, "Contents", "Info.plist")
    asar = os.path.join(app_path, "Contents", "Resources", "app.asar")
    try:
        with open(info_plist, "rb") as fh:
            original = fh.read()
        info = plistlib.loads(original)
    except (OSError, plistlib.InvalidFileException) as exc:
        _die(f"Unable to read Info.plist: {info_plist} ({exc})")

    integrity = info.get("ElectronAsarIntegrity")
    entry = integrity.get("Resources/app.asar") if isinstance(integrity, dict) else None
    if not isinstance(entry, dict):
        print("    ElectronAsarIntegrity is not declared; no update needed.")
        return
    if str(entry.get("algorithm", "SHA256")).upper() != "SHA256":
        _die("Info.plist uses an unsupported ASAR integrity algorithm.")

    try:
        entry["algorithm"] = "SHA256"
        entry["hash"] = _asar_header_hash(asar)
    except ValueError as exc:
        _die(str(exc))

    plist_format = plistlib.FMT_BINARY if original.startswith(b"bplist") else plistlib.FMT_XML
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="wb", dir=os.path.dirname(info_plist), delete=False) as fh:
            temp_path = fh.name
            plistlib.dump(info, fh, fmt=plist_format, sort_keys=False)
        os.chmod(temp_path, stat.S_IMODE(os.stat(info_plist).st_mode))
        os.replace(temp_path, info_plist)
    except OSError as exc:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)
        _die(f"Unable to update Info.plist: {exc}")
    print("    ElectronAsarIntegrity updated.")


def _sanitize_macos_entitlements(entitlements):
    sanitized = {
        key: value
        for key, value in entitlements.items()
        if key.startswith("com.apple.security.")
        and key != "com.apple.security.application-groups"
    }
    # The outer ad-hoc signature has no Team ID, while nested vendor frameworks
    # retain OpenAI's signature. Allow those unchanged frameworks to load.
    sanitized["com.apple.security.cs.disable-library-validation"] = True
    return sanitized


def _macos_adhoc_entitlements(app_path):
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="wb", suffix=".plist", delete=False) as fh:
            temp_path = fh.name
        rc, output = run_cmd([
            "codesign", "-d", "--xml", "--entitlements", temp_path, app_path,
        ], capture=True)
        if rc != 0:
            os.unlink(temp_path)
            temp_path = None
            _die(f"Unable to read macOS app entitlements: {output}")
        with open(temp_path, "rb") as fh:
            entitlements = plistlib.load(fh)
        if not isinstance(entitlements, dict):
            raise plistlib.InvalidFileException("entitlements are not a dictionary")
        entitlements = _sanitize_macos_entitlements(entitlements)
        with open(temp_path, "wb") as fh:
            plistlib.dump(entitlements, fh, sort_keys=False)
        return temp_path
    except (OSError, plistlib.InvalidFileException) as exc:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)
        _die(f"Unable to prepare macOS ad-hoc entitlements: {exc}")


# ================================================================
# Step 7: platform-specific finalization
# ================================================================
def step_finish_macos(app_path):
    print("[7] Signing and verifying the macOS copy...")
    if DRY_RUN:
        print("    [DRY-RUN] Signing skipped")
        return

    rc, output = run_cmd(
        ["xattr", "-dr", "com.apple.quarantine", app_path], capture=True)
    if rc != 0:
        _die(f"Unable to remove quarantine from the patched copy: {output}")

    entitlements_path = _macos_adhoc_entitlements(app_path)
    try:
        rc, output = run_cmd([
            "codesign", "--force", "--sign", "-",
            "--preserve-metadata=identifier,flags,runtime",
            "--entitlements", entitlements_path,
            app_path,
        ], capture=True)
        if rc != 0:
            _die(f"macOS signing failed: {output}")
    finally:
        if os.path.exists(entitlements_path):
            os.unlink(entitlements_path)

    rc, output = run_cmd([
        "codesign", "--verify", "--deep", "--strict", "--verbose=2", app_path,
    ], capture=True)
    if rc != 0:
        _die(f"macOS signature verification failed: {output}")
    print("    Signature created and passed strict verification.")


def step_shortcut_windows(exe_path, work_dir):
    print("[7] Creating a desktop shortcut...")
    desktop  = os.path.join(os.path.expanduser("~"), "Desktop")
    is_chatgpt = os.path.basename(exe_path).lower() == "chatgpt.exe"
    display_name = "ChatGPT Codex" if is_chatgpt else "Codex"
    shortcut = os.path.join(desktop, f"{display_name} (Patched).lnk")
    if DRY_RUN:
        print(f"    [DRY-RUN] {shortcut}")
        return
    ps = (
        f"$wsh=New-Object -ComObject WScript.Shell;"
        f"$lnk=$wsh.CreateShortcut('{shortcut}');"
        f"$lnk.TargetPath='{exe_path}';"
        f"$lnk.WorkingDirectory='{work_dir}';"
        f"$lnk.IconLocation='{exe_path},0';"
        f"$lnk.Description='{display_name} (API Key Features Unlocked)';"
        f"$lnk.Save()"
    )
    run_cmd(["powershell", "-NoProfile", "-Command", ps])
    print(f"    Created: {shortcut}")


# ================================================================
# Main workflow
# ================================================================
print()
print("==========================================")
print("  ChatGPT Codex API Key Feature Unlocker")
print("==========================================")
print()

if (args.app or args.output) and not IS_MACOS:
    _die("--app and --output are supported only on macOS.")

if args.assets:
    # -- Patch only JavaScript assets for debugging or version updates --
    if args.app or args.output:
        _die("--assets cannot be combined with --app or --output.")
    print(f"[MANUAL] Patching only JavaScript assets: {args.assets}")
    if not os.path.isdir(args.assets):
        _die(f"Directory not found: {args.assets}")
    step_patch_js(args.assets)

else:
    # -- Full workflow -------------------------------------------------
    source_root, resources_dir, exe_path, is_store = step_detect()
    macos_kill_paths = ()
    if IS_MACOS:
        macos_kill_paths = (
            source_root,
            _macos_patched_path(source_root, args.output),
        )
    step_kill_codex(exe_path, macos_kill_paths)

    if IS_MACOS:
        # Patch and sign only the independent copy. Retain the official OpenAI
        # signature for Appshots and original TCC identity fallback.
        work_root, resources_dir, exe_path = step_copy_macos(
            source_root, args.output)
    elif IS_WINDOWS and is_store:
        patch_root, resources_dir, exe_path = step_copy_store(source_root)
        work_root = patch_root
    else:
        work_root = source_root

    step_extract_asar(resources_dir)

    assets = os.path.join(resources_dir, "app", "webview", "assets")
    if DRY_RUN and not os.path.isdir(assets):
        print("[5] [DRY-RUN] Target copy does not exist; JS inspection skipped")
    else:
        if not os.path.isdir(assets):
            _die(f"Assets directory not found: {assets}")
        step_patch_js(assets)
        _require_successful_js_patch()

    # Repack the patched app/ directory because owl loads only app.asar and
    # does not support an app/ directory fallback.
    step_repack_asar(resources_dir)

    if IS_MACOS:
        # Preserve Owl/Electron ASAR integrity metadata without modifying the
        # large runtime framework.
        step_update_macos_asar_integrity(work_root)
        step_finish_macos(work_root)
    else:
        step_fuses(exe_path)
        if IS_WINDOWS and is_store:
            step_shortcut_windows(exe_path, work_root)

# ================================================================
# Summary report
# ================================================================
total = len(results["applied"]) + len(results["skipped"]) + len(results["failed"])
print()
print("=" * 50)
print("Patch report")
print("=" * 50)
print(f"  Total {total}  |  Applied {len(results['applied'])}  |"
      f"  Skipped {len(results['skipped'])}  |  Failed {len(results['failed'])}")

if results["applied"]:
    print("\n  Applied:")
    for r in results["applied"]:
        print(f"    + {r}")
if results["skipped"]:
    print("\n  Skipped (already applied):")
    for r in results["skipped"]:
        print(f"    - {r}")
if results["failed"]:
    print("\n  Failed (manual action required):")
    for r in results["failed"]:
        print(f"    x {r}")
    print("  -> See the version-update troubleshooting guide in SKILL.md")
    sys.exit(1)

print()
if not args.assets and DRY_RUN:
    print("  Dry run complete: no processes stopped and no files copied or packed.")
elif not args.assets:
    if IS_WINDOWS and is_store:
        shortcut_name = (
            "ChatGPT Codex (Patched)"
            if os.path.basename(exe_path).lower() == "chatgpt.exe"
            else "Codex (Patched)"
        )
        print(f"  Patch complete. Launch the '{shortcut_name}' desktop shortcut.")
        print(f"  Or run directly: {exe_path}")
    elif IS_WINDOWS:
        print(f"  Patch complete. Launch {os.path.basename(exe_path)}.")
    elif IS_MACOS:
        print(f"  Patch complete. Launch the patched copy: {work_root}")
        print(f"  Official {source_root} remains unchanged for signature/TCC fallback.")
        print("  Computer Use appears as 'Codex Computer Use' in System Settings.")
print()
