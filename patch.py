#!/usr/bin/env python3
"""
ChatGPT Codex — API Key 模式全功能解锁
跨平台一键脚本 (macOS / Windows)，路径全部自动检测，无需硬编码。

用法:
    python3 patch.py                        # 自动检测安装位置，执行完整流程
    python3 patch.py --assets /path/assets  # 仅对指定目录重新打 JS 补丁（跳过 asar/fuses）
    python3 patch.py --dry-run              # 预演（不写入任何文件）
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
# 参数解析
# ================================================================
parser = argparse.ArgumentParser(description="ChatGPT Codex API Key 模式全功能解锁")
parser.add_argument("--assets", metavar="DIR",
                    help="手动指定 webview/assets 目录，跳过 asar 解包 / fuses 步骤")
parser.add_argument("--app", metavar="APP",
                    help="macOS: 手动指定官方 ChatGPT/Codex .app 路径")
parser.add_argument("--output", metavar="APP",
                    help="macOS: 手动指定补丁副本的 .app 路径")
parser.add_argument("--dry-run", action="store_true",
                    help="预演模式：仅打印操作，不写入文件")
args = parser.parse_args()

IS_MACOS   = sys.platform == "darwin"
IS_WINDOWS = sys.platform == "win32"
DRY_RUN    = args.dry_run

WINDOWS_EXE_NAMES = ("ChatGPT.exe", "Codex.exe")
WINDOWS_INSTALL_NAMES = ("ChatGPT", "Codex")
# 当前 ChatGPT 品牌版本仍沿用 OpenAI.Codex 包身份；保留未来身份变更的回退。
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
    print("[DRY-RUN] 预演模式，不会实际修改文件\n")


# ================================================================
# 工具：运行子进程
# ================================================================
def run_cmd(cmd, capture=False):
    """执行命令，返回 (returncode, output_str)。"""
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
        raise ValueError(f"无法读取 MSIX manifest: {manifest}") from exc

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
    raise ValueError(f"MSIX 中未找到带 Codex 资源的应用入口: {manifest}")


# ================================================================
# 步骤 2: 关闭已验证的 ChatGPT Codex
# ================================================================
def step_kill_codex(exe_path, macos_app_paths=()):
    print("[2] 关闭 ChatGPT Codex 进程...")
    if DRY_RUN:
        print("    [DRY-RUN] 跳过关闭进程")
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
# 步骤 1: 定位并验证安装目录
# ================================================================
def step_detect():
    """
    返回 (source_root, resources_dir, exe_path, is_store)
    source_root  : 安装根目录（Store 版为只读 MSIX 目录）
    resources_dir: 含 app.asar 的可写 resources 目录
    exe_path     : 可执行文件路径（可写位置）
    is_store     : 是否为 Store 版（需先复制到可写目录）
    """
    print("[1] 定位 ChatGPT Codex 安装目录...")

    if IS_MACOS:
        app_paths = MACOS_APP_PATHS
        if args.app:
            app_paths = (os.path.abspath(os.path.expanduser(args.app)),)
        for app in app_paths:
            resources = os.path.join(app, "Contents", "Resources")
            exe = _macos_executable(app) if os.path.isdir(app) else None
            if exe is None or not _is_codex_resources(resources):
                continue
            print(f"  检测到 macOS 版: {app} ({os.path.basename(exe)})")
            return app, resources, exe, False
        searched = "、".join(app_paths)
        _die(f"未找到有效的 macOS ChatGPT/Codex app。已检查: {searched}")

    if IS_WINDOWS:
        local = os.environ.get("LOCALAPPDATA", "")
        if not local:
            _die("LOCALAPPDATA 环境变量未设置。")

        # ── 传统安装版 ──────────────────────────────────────
        for install_name in WINDOWS_INSTALL_NAMES:
            trad_root = os.path.join(local, "Programs", install_name)
            trad_res = os.path.join(trad_root, "resources")
            trad_exe = _windows_executable(trad_root)
            if (trad_exe is not None and _is_codex_resources(trad_res)):
                print(f"  检测到传统安装版: {trad_root} ({os.path.basename(trad_exe)})")
                return trad_root, trad_res, trad_exe, False

        # ── Microsoft Store 版 (MSIX) ───────────────────────
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
                print(f"  检测到 Store 版 (MSIX): {store_root} [{package_name}]")
                return store_root, resources, exe, True

    _die("未找到 ChatGPT Codex 安装目录。请确认应用已安装（Store 版或传统安装版）。")


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
        raise error
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
# 步骤 3 (仅 Store 版): 复制到可写目录
# ================================================================
def step_copy_store(store_root):
    """
    将 Store 版 app 目录用 robocopy /COPY:DAT 复制到可写位置。
    返回 (patch_root, resources_dir, exe_path)
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

    print(f"[3] 复制 app 目录（约 300 MB，请稍候）...")
    print(f"    {src}")
    print(f"    -> {patch_root}")

    if DRY_RUN:
        print("    [DRY-RUN] 跳过复制")
        return patch_root, resources, exe

    if os.path.exists(patch_root):
        print("    正在关闭旧补丁副本的后台进程并清理目录...")
        try:
            _remove_windows_store_copy(patch_root)
        except OSError as exc:
            _die(
                f"无法清理旧补丁目录: {patch_root}\n"
                "请确认从该目录启动的 ChatGPT/Codex 及 Computer Use 已关闭；"
                "若它以管理员身份运行，请以管理员身份重新运行本脚本。"
                f"\n原始错误: {exc}"
            )
    os.makedirs(patch_root, exist_ok=True)

    # /COPY:DAT 只复制数据/属性/时间戳，跳过 EFS 加密属性（WindowsApps 目录限制）
    # /NP 不显示进度百分比（避免刷屏），但保留目录/文件列表让用户知道在工作
    print("    复制中，这可能需要 1-2 分钟...")
    rc, _ = run_cmd(
        ["robocopy", src, patch_root,
         "/E", "/COPY:DAT", "/NP", "/NDL", "/NJH", "/NJS"]
    )
    if rc >= 8:
        _die(f"robocopy 失败 (exit {rc})，请以管理员身份运行。")

    print("    复制完成。")
    return patch_root, resources, exe


# ================================================================
# 步骤 4: 备份 + 提取 app.asar
# ================================================================
# 说明: Codex 使用 OpenAI 定制的 "owl" Electron 运行时，它只从
# resources/app.asar 加载，不会像标准 Electron 那样回退到 app/ 文件夹，
# 且不暴露标准 fuse wire（无法用 @electron/fuses 关闭 OnlyLoadAppFromAsar）。
# 因此流程为: 从 app.asar.bak(原始) 提取 -> 打补丁 -> 重新打包回 app.asar。
def step_extract_asar(resources_dir):
    print("[4] 提取 app.asar...")

    asar     = os.path.join(resources_dir, "app.asar")
    asar_bak = os.path.join(resources_dir, "app.asar.bak")
    app_dir  = os.path.join(resources_dir, "app")

    if DRY_RUN:
        print("    [DRY-RUN] 跳过 asar 检查和提取")
        return

    if not os.path.isfile(asar) and not os.path.isfile(asar_bak):
        _die(f"未找到 app.asar: {asar}")

    # 备份原始 asar（仅首次）。
    if not os.path.isfile(asar_bak):
        shutil.copy2(asar, asar_bak)
        print("    已备份 app.asar -> app.asar.bak")

    # 从 app.asar 提取。electron/asar 会自动合并同名 sidecar
    # (app.asar.unpacked) 中的原生模块文件，因此必须从 app.asar 提取，
    # 而非从 app.asar.bak（其 sidecar 名不匹配会导致 ENOENT）。
    # 补丁已幂等，即使 app.asar 已被打过补丁，重新提取+打补丁也安全。
    if os.path.isdir(app_dir):
        shutil.rmtree(app_dir)

    rc, _ = run_cmd(["npx", "--yes", ASAR_PACKAGE, "e", asar, app_dir])
    if rc != 0:
        _die("asar 提取失败，请确认 Node.js 已安装（npx 可用）。")
    print("    提取到 app/ 完成。")


# ================================================================
# 步骤 4.5: 重新打包 app/ -> app.asar
# ================================================================
def step_repack_asar(resources_dir):
    print("\n[5.5] 重新打包 app/ -> app.asar...")

    asar      = os.path.join(resources_dir, "app.asar")
    app_dir   = os.path.join(resources_dir, "app")
    unpacked  = os.path.join(resources_dir, "app.asar.unpacked")

    if DRY_RUN:
        print("    [DRY-RUN] 跳过重新打包")
        return

    if not os.path.isdir(app_dir):
        _die(f"app/ 目录不存在，无法打包: {app_dir}")

    # 清理旧的 unpacked，避免残留
    if os.path.isdir(unpacked):
        shutil.rmtree(unpacked)

    # 原生模块 (.node) 及 node-pty/better-sqlite3 必须解包到磁盘，
    # 否则 Electron 无法 dlopen 原生扩展。
    rc, _ = run_cmd([
        "npx", "--yes", ASAR_PACKAGE, "pack", app_dir, asar,
        "--unpack-dir", "{**/node_modules/node-pty,**/node_modules/better-sqlite3}",
        "--unpack", "**/*.node",
    ])
    if rc != 0:
        _die("asar 打包失败。")

    # 清理可能残留的旧式 app.asar1（历史版本产物）
    asar1 = os.path.join(resources_dir, "app.asar1")
    if os.path.isfile(asar1):
        os.remove(asar1)

    print("    打包完成，补丁已写入 app.asar（原生模块已解包）。")


# ================================================================
# 步骤 5: JS 补丁
# ================================================================
results = {"applied": [], "skipped": [], "failed": []}


def _find(base, pattern):
    return glob.glob(os.path.join(base, pattern))


def apply_patch(fp, name, find_str, replace_str, regex=None, replace_fn=None, skip_regex=None):
    with open(fp, encoding="utf-8") as f:
        content = f.read()
    bn = os.path.basename(fp)

    # 使用自定义 skip_regex 检测补丁是否已应用（优先级最高）
    if skip_regex and re.search(skip_regex, content):
        results["skipped"].append(f"{bn}: {name}")
        print(f"    [SKIP] {name}")
        return

    # 使用 replace_str 检测补丁是否已应用（向后兼容）
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
            mark_missing(name, "未找到目标文件")
        return None
    mark_missing(name, f"找到 {len(files)} 个候选，拒绝不明确修改")
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
        ("browser_use", "isBrowserAgentGateEnabled", "内置 Browser 可用性"),
        ("browser_use_external", "isExternalBrowserUseGateEnabled",
         "外部 Browser 可用性"),
        ("computer_use", "isComputerUseGateEnabled", "Computer Use 可用性"),
    )
    auth_context = _react_auth_context(content)
    if auth_context is None:
        mark_missing(f"{bn}: Browser / Computer Use API key 范围", "认证上下文不明确")
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
        mark_missing(f"{bn}: Browser / Computer Use API key 范围", "认证 hook 不明确")
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
            reason = "目标结构不匹配"
            if len(candidates) > 1:
                reason = f"找到 {len(candidates)} 个目标"
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
        mark_missing(f"{bn}: {patch_name}", "认证 hook 不明确")
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
        reason = "目标结构不匹配"
        if len(candidates) > 1:
            reason = f"找到 {len(candidates)} 个目标"
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
        "Windows 任务栏独立身份",
        required=feature_present,
    )
    if target is None:
        return
    apply_patch(
        target,
        "Windows 任务栏独立身份",
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
        reason = "目标结构不匹配"
        match_count = len(authorization_matches) + len(
            patched_authorization_matches)
        if match_count > 1:
            reason = f"找到 {match_count} 个授权目标"
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
        reason = "未找到唯一的 chmod 依赖"
        if len(chmod_sources) > 1:
            reason = f"找到 {len(chmod_sources)} 个 chmod 依赖"
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
        reason = "未找到唯一的 Browser socket listener"
        if len(listener_matches) > 1:
            reason = f"找到 {len(listener_matches)} 个 Browser socket listener"
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
        reason = "目标结构不匹配"
        if len(target_signatures) > 1:
            reason = f"找到 {len(target_signatures)} 个目标函数"
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
        "隐藏模型列表解锁",
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
    condition_pattern = re.compile(
        r'if\((?P<mode>[a-zA-Z_$]+)\?'
        r'(?P<allowed>[a-zA-Z_$]+)\.has\('
        r'(?P<model>[a-zA-Z_$]+)\.model\):!'
        r'(?P=model)\.hidden\)\{'
    )
    patched_pattern = re.compile(
        rf'if\({re.escape(auth)}===`apikey`\|\|\('
        r'[a-zA-Z_$]+\?[a-zA-Z_$]+\.has\('
        r'([a-zA-Z_$]+)\.model\):!\1\.hidden\)\)\{'
    )
    if patched_pattern.search(function_body):
        results["skipped"].append(f"{bn}: 隐藏模型列表解锁")
        print("    [SKIP] 隐藏模型列表解锁")
        return

    condition_match = condition_pattern.search(function_body)
    if condition_match is None:
        mark_missing(f"{bn}: 隐藏模型列表解锁", "目标结构不匹配")
        return

    mode = condition_match.group("mode")
    allowed = condition_match.group("allowed")
    model = condition_match.group("model")
    patched = (
        f"if({auth}===`apikey`||"
        f"({mode}?{allowed}.has({model}.model):!{model}.hidden)){{"
    )
    if not DRY_RUN:
        start = signature_match.end() + condition_match.start()
        end = signature_match.end() + condition_match.end()
        content = content[:start] + patched + content[end:]
        with open(fp, "w", encoding="utf-8") as fh:
            fh.write(content)
    results["applied"].append(f"{bn}: 隐藏模型列表解锁 (regex)")
    print("    [OK]   隐藏模型列表解锁 (regex)")


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
    patch_name = f"{bn}: 推理强度列表解锁"
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
        mark_missing(patch_name, "目标结构不匹配")
        return False

    signature_match, aliases = target_signatures[0]
    scope_start = signature_match.start()
    scope_end = _js_block_end(content, signature_match.end() - 1)
    if scope_end is None:
        mark_missing(patch_name, "目标结构不匹配")
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
        mark_missing(patch_name, "目标结构不匹配")
        return False

    ultra_match = (ultra_original_matches or ultra_patched_matches)[0]
    enabled_match = (enabled_original_matches or enabled_patched_matches)[0]
    if enabled_match.group("efforts") != ultra_match.group("target"):
        mark_missing(patch_name, "目标结构不匹配")
        return False
    if enabled_patched_matches:
        shadowed_names = {
            auth,
            enabled,
            enabled_match.group("validator"),
        }
        if enabled_match.group("effort") in shadowed_names:
            mark_missing(patch_name, "目标结构不匹配")
            return False
    if validate_only:
        return True
    if ultra_patched_matches and enabled_patched_matches:
        results["skipped"].append(patch_name)
        print("    [SKIP] 推理强度列表解锁")
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
        mark_missing(patch_name, "补丁后校验失败")
        return False

    patched_content = content[:scope_start] + patched_scope + content[scope_end:]
    if not DRY_RUN:
        with open(fp, "w", encoding="utf-8") as fh:
            fh.write(patched_content)
    results["applied"].append(f"{patch_name} (regex)")
    print("    [OK]   推理强度列表解锁 (regex)")
    return True


def step_patch_js(assets):
    print(f"[5] 应用 JS 补丁...")
    print(f"    {assets}\n")
    main_build = _desktop_main_build(assets)

    # ── 模块 1: Fast 模式 / 服务层级 (2 补丁) ────────────────────
    # 新版 (26.602+): 逻辑迁移到 use-service-tier-settings-*.js
    #   函数 A 中 a=i?.authMethod===`chatgpt` 门控 isServiceTierAllowed，
    #   将 a 强制为真即解锁 apikey 的 Fast / 服务层级选择。
    # 旧版: use-is-fast-mode-enabled-*.js (含 canUseFastMode)
    print("  [模块 1] Fast 模式 / 服务层级")
    files = _find(assets, "use-service-tier-settings-*.js")
    if not files:
        files = _find(assets, "use-is-fast-mode-enabled-*.js")
    if not files:
        for f in glob.glob(os.path.join(assets, "*.js")):
            with open(f, encoding="utf-8") as fh:
                c = fh.read()
            if "isServiceTierAllowed" in c and "authMethod===`chatgpt`" in c:
                files = [f]; break
    fast_ui_fp = _single_patch_target(files, "服务层级授权门控")
    if fast_ui_fp is not None:
        # 只把 apikey 加入允许范围，不改变 Copilot/Bedrock 等其他认证模式。
        # 同时识别并收敛旧版 true|| 补丁。
        apply_patch(fast_ui_fp, "服务层级授权门控",
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

    # 26.707+: 真正构造请求时会再次把服务层级限制为 chatgpt。
    request_tier_files = _find(assets, "read-service-tier-for-request-*.js")
    if not request_tier_files:
        for f in glob.glob(os.path.join(assets, "*.js")):
            with open(f, encoding="utf-8") as fh:
                c = fh.read()
            if "Failed to read service tier for request" in c:
                request_tier_files = [f]
                break
    request_tier_fp = _single_patch_target(
        request_tier_files, "Fast 请求服务层级门控", required=False)
    if request_tier_fp is not None:
        apply_patch(request_tier_fp, "Fast 请求服务层级门控",
            None, None,
            r'if\(([a-zA-Z_$]+)!==`chatgpt`\)return!1;',
            lambda m: f"if({m.group(1)}!==`chatgpt`&&{m.group(1)}!==`apikey`)return!1;",
            skip_regex=r'if\(([a-zA-Z_$]+)!==`chatgpt`&&\1!==`apikey`\)return!1;')

    # ── 模块 2: 最新模型 / 推理强度列表 (2 补丁) ────────────────
    # API key 模式没有 ChatGPT Statsig 的 hidden-model 白名单。
    # list-models-for-host 已请求 includeHidden=true，这里放开默认分支即可展示
    # 后端实际返回的新模型，同时保留 ChatGPT 账号模式的显式白名单逻辑。
    print("\n  [模块 2] 最新模型 / 推理强度列表")
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
            model_filter_files, "隐藏模型列表解锁")
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
            legacy_model_files, "模型可用性检查（旧版）")
        if legacy_model_fp is not None:
            apply_patch(legacy_model_fp, "模型可用性检查（旧版）",
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

    # ── 模块 3: i18n 多语言 (1 补丁) ────────────────────────────
    # 新版: app-main 中 React Compiler 形式 s=a?.get(`enable_i18n`,!1)
    # 旧版: r=(0,Q.useMemo)(()=>n?.get(`enable_i18n`,!1),[n])
    # 注: 旧版"插件侧边栏 (pluginsDisabledTooltip)"已被移除，
    #     插件门控现由模块 4 的 ge() 函数统一控制。
    print("\n  [模块 3] i18n 多语言")
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
        files, "i18n 多语言强制启用", required=False)
    if i18n_fp is not None:
        apply_patch(i18n_fp, "i18n 多语言强制启用",
            None, None,
            r'([a-zA-Z_$]+)=([a-zA-Z_$]+)\?\.get\(`enable_i18n`,!1\)',
            lambda m: f"{m.group(1)}=true||{m.group(2)}?.get(`enable_i18n`,!1)",
            skip_regex=r'=true\|\|[a-zA-Z_$]+\?\.get\(`enable_i18n`,!1\)')

    # ── 模块 4: Browser / Computer Use (5 补丁) ──────────────────
    # API key 会话没有 ChatGPT Statsig 用户上下文，三个桌面可用性 gate
    # 会返回 statsig-disabled，主进程随后会从 bundled marketplace 移除
    # browser/chrome/computer-use。这里只让 API key 与原 gate 取 OR；其他
    # 认证模式保留 Statsig 结果，平台、WSL、app-server experimental feature
    # 与插件配置检查也仍由原逻辑执行。
    print("\n  [模块 4] Browser / Computer Use")
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
        "Browser / Computer Use 可用性",
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

    # macOS 补丁副本的外层 app 是 ad-hoc 签名。Browser native addon 会沿
    # responsible-process 链返回 missing-code-signing-identity，即便实际的
    # node -> codex sandbox -> node_repl 都保留 OpenAI 签名。只对此原因降级，
    # 继续保留 untrusted identity 与 missing fd 的拒绝路径。
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

    # ── 模块 5: 旧版插件连接器 UI 门控 (1 补丁) ─────────────────
    print("\n  [模块 5] 旧版插件连接器 UI 门控")
    native_plugins_fp = _native_apikey_plugins_file(assets)
    files = _find(assets, "check-plugin-availability-*.js")
    if files:
        for fp in files:
            apply_patch(fp, "旧版插件连接器 UI 门控",
                "(i=`connector-unavailable`)", "false&&(i=`connector-unavailable`)",
                r'(?<!&&)\(([a-zA-Z_$])=`connector-unavailable`\)',
                lambda m: f"false&&({m.group(1)}=`connector-unavailable`)",
                skip_regex=r'false&&\([a-zA-Z_$]=`connector-unavailable`\)')
    elif native_plugins_fp is not None:
        mark_satisfied(
            native_plugins_fp,
            "旧版插件连接器 UI 门控",
            "新版已移除该旧补丁点",
        )

    # ── 模块 6: 品牌视觉 + 插件市场门控 (1 补丁) ────────────────
    # 新版 (26.602+): use-plugins-*.js 中 function ge(e){return e!==`chatgpt`}
    #   该函数同时控制品牌视觉与插件侧边栏可用性。
    #   注意: 函数名(ge) 与参数名(e) 不再相同。
    # 旧版: plugin-auth-*.js / gradient-*.js
    print("\n  [模块 6] 品牌视觉 + 插件市场门控")
    if native_plugins_fp is not None:
        mark_satisfied(
            native_plugins_fp,
            "品牌视觉/插件市场统一",
            "新版已原生支持 API key",
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
            # function ge(e){return e!==`chatgpt`}  →  {return false&&e!==`chatgpt`}
            apply_patch(fp, "品牌视觉/插件统一",
                None, None,
                r'function ([a-zA-Z_$]+)\(([a-zA-Z_$]+)\)\{return \2!==`chatgpt`\}',
                lambda m: f"function {m.group(1)}({m.group(2)}){{return false&&{m.group(2)}!==`chatgpt`}}",
                skip_regex=r'function [a-zA-Z_$]+\([a-zA-Z_$]+\)\{return false&&[a-zA-Z_$]+!==`chatgpt`\}')

    # ── 模块 7: 语音输入 (1 补丁) ────────────────────────────────
    # 新版 (26.602+): use-is-dictation-supported-*.js 中 n&&t.authMethod===`chatgpt`
    # 旧版: annotation-comment-editor-card-*.js
    print("\n  [模块 7] 语音输入")
    files = _find(assets, "use-is-dictation-supported-*.js")
    if not files:
        files = _find(assets, "annotation-comment-editor-card-*.js")
    if not files:
        # 精确匹配：含 dictation 判定模式的文件，避免误选 app-main
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
        apply_patch(fp, "语音输入解锁",
            None, None,
            r'([a-zA-Z_$]+)&&([a-zA-Z_$]+)\.authMethod===`chatgpt`(?!\|\|)',
            lambda m: f"{m.group(1)}&&({m.group(2)}.authMethod===`chatgpt`||{m.group(2)}.authMethod===`apikey`)",
            skip_regex=r'authMethod===`chatgpt`\|\|[a-zA-Z_$]+\.authMethod===`apikey`')

    # ── 模块 8: 用量设置 (1 补丁) ────────────────────────────────
    print("\n  [模块 8] 用量设置")
    files = _find(assets, "use-usage-settings-access-*.js")
    if not files:
        for f in glob.glob(os.path.join(assets, "*.js")):
            with open(f, encoding="utf-8") as fh:
                if re.search(r'let [a-zA-Z_$]+=[a-zA-Z_$]+===`chatgpt`', fh.read()):
                    files = [f]; break
    for fp in files:
        apply_patch(fp, "用量设置解锁",
            "let r=e===`chatgpt`", "let r=e===`chatgpt`||e===`apikey`",
            r'let\s+([a-zA-Z_$]+)=([a-zA-Z_$]+)===`chatgpt`(?!\|\|)',
            lambda m: f"let {m.group(1)}={m.group(2)}===`chatgpt`||{m.group(2)}===`apikey`",
            skip_regex=r'let [a-zA-Z_$]+=[a-zA-Z_$]+===`chatgpt`\|\|[a-zA-Z_$]+===`apikey`')

    # Store/传统安装与独立副本共用官方 AUMID 时，任务栏固定项会重新打开
    # Store 版。只在 Windows 副本中分配独立身份，macOS bundle 保持不变。
    if IS_WINDOWS and main_build and os.path.isdir(main_build):
        print("\n  [模块 9] Windows 任务栏身份")
        apply_windows_app_user_model_id_patch(main_build)


def _require_successful_js_patch():
    """Stop a full build before packaging or signing any partial JS result."""
    if results["failed"]:
        _die("JS 补丁校验失败，已停止重打包与签名。")


# ================================================================
# 步骤 6: 禁用 Electron fuses
# ================================================================
def step_fuses(exe_path):
    print("\n[6] 禁用 Electron fuses...")
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
            print(f"    [跳过] {flag}")
        else:
            print(f"    {flag}")

    if no_sentinel:
        # OpenAI 的 owl Electron 构建未暴露标准 fuse wire（找不到 sentinel）。
        # 这是预期情况，且不影响补丁：补丁已重新打包进 app.asar，
        # 通过正常的 asar 加载路径生效，无需修改任何 fuse。
        print("    注: 此 Electron 构建未暴露 fuses（找不到 sentinel），属正常现象。")
        print("        补丁已写入 app.asar，无需 fuses 即可生效。")


# ================================================================
# 步骤 3 (仅 macOS): 复制官方 app 到独立副本
# ================================================================
# 说明: macOS 主 app 的 TCC 权限(屏幕录制/辅助功能/自动化)绑定到代码的
# 签名身份(Designated Requirement)，而非 bundle id。若直接修改官方 app
# 并 ad-hoc 重签名，系统会把它当成另一个 app。官方 app 因此原样保留，
# 作为 Appshots 与原始主 app TCC 身份的回退；补丁只写入独立副本。
# Computer Use 使用另一个保持 OpenAI 签名的辅助 app，不依赖副本的 Team ID。


def _macos_patched_path(official_app, requested_output=None):
    source_exe = _macos_executable(official_app)
    if source_exe is None:
        _die(f"未找到 ChatGPT/Codex 可执行文件: {official_app}")
    patched_name = (
        "ChatGPT-Codex-Patched.app"
        if os.path.basename(source_exe) == "ChatGPT"
        else "Codex-Patched.app"
    )
    if requested_output:
        patched_app = os.path.abspath(os.path.expanduser(requested_output))
        if not patched_app.lower().endswith(".app"):
            _die("--output 必须是以 .app 结尾的路径。")
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
            _die("补丁副本路径不能与官方 app 相同。")
    except OSError as exc:
        _die(f"无法验证 macOS app 路径: {exc}")

    try:
        common = os.path.commonpath((source_real, output_real))
    except ValueError as exc:
        _die(f"无法验证 macOS app 路径: {exc}")
    if common in (source_real, output_real):
        _die("官方 app 与补丁副本路径不能相同或互相包含。")


def step_copy_macos(official_app, requested_output=None):
    """
    将官方 ChatGPT/Codex app 复制到独立 Patched 副本
    （保留所有属性/符号链接/扩展属性）。
    返回 (patched_app, resources_dir, exe_path)
    """
    source_exe = _macos_executable(official_app)
    if source_exe is None:
        _die(f"未找到 ChatGPT/Codex 可执行文件: {official_app}")
    exe_name = os.path.basename(source_exe)
    patched_app = _macos_patched_path(official_app, requested_output)
    resources   = os.path.join(patched_app, "Contents", "Resources")
    exe         = os.path.join(patched_app, "Contents", "MacOS", exe_name)
    _validate_macos_copy_paths(official_app, patched_app)

    print("[3] 复制官方 app 到独立副本...")
    print(f"    {official_app}")
    print(f"    -> {patched_app}")
    print(f"    (官方 {official_app} 保持不变，保留原始签名与 TCC 身份)")

    if DRY_RUN:
        print("    [DRY-RUN] 跳过复制")
        return patched_app, resources, exe

    os.makedirs(os.path.dirname(patched_app), exist_ok=True)
    if os.path.exists(patched_app):
        shutil.rmtree(patched_app)

    # 完整保留 bundle 结构、符号链接与扩展属性；收尾时仅移除 quarantine。
    rc, _ = run_cmd(["ditto", official_app, patched_app])
    if rc != 0:
        _die(f"复制 app 失败，请确认目标目录可写: {os.path.dirname(patched_app)}")

    print("    复制完成。")
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
        raise ValueError(f"无法读取 ASAR header: {asar_path}") from exc
    return hashlib.sha256(header).hexdigest()


def step_update_macos_asar_integrity(app_path):
    print("\n[6] 更新 macOS ASAR 完整性信息...")
    if DRY_RUN:
        print("    [DRY-RUN] 跳过完整性信息更新")
        return

    info_plist = os.path.join(app_path, "Contents", "Info.plist")
    asar = os.path.join(app_path, "Contents", "Resources", "app.asar")
    try:
        with open(info_plist, "rb") as fh:
            original = fh.read()
        info = plistlib.loads(original)
    except (OSError, plistlib.InvalidFileException) as exc:
        _die(f"无法读取 Info.plist: {info_plist} ({exc})")

    integrity = info.get("ElectronAsarIntegrity")
    entry = integrity.get("Resources/app.asar") if isinstance(integrity, dict) else None
    if not isinstance(entry, dict):
        print("    未声明 ElectronAsarIntegrity，无需更新。")
        return
    if str(entry.get("algorithm", "SHA256")).upper() != "SHA256":
        _die("Info.plist 使用了不支持的 ASAR 完整性算法。")

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
        _die(f"无法更新 Info.plist: {exc}")
    print("    ElectronAsarIntegrity 已更新。")


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
            _die(f"无法读取 macOS app entitlements: {output}")
        with open(temp_path, "rb") as fh:
            entitlements = plistlib.load(fh)
        if not isinstance(entitlements, dict):
            raise plistlib.InvalidFileException("entitlements 不是字典")
        entitlements = _sanitize_macos_entitlements(entitlements)
        with open(temp_path, "wb") as fh:
            plistlib.dump(entitlements, fh, sort_keys=False)
        return temp_path
    except (OSError, plistlib.InvalidFileException) as exc:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)
        _die(f"无法准备 macOS ad-hoc entitlements: {exc}")


# ================================================================
# 步骤 7: 平台收尾
# ================================================================
def step_finish_macos(app_path):
    print("[7] 重新签名并验证 (macOS 副本)...")
    if DRY_RUN:
        print("    [DRY-RUN] 跳过签名")
        return

    rc, output = run_cmd(
        ["xattr", "-dr", "com.apple.quarantine", app_path], capture=True)
    if rc != 0:
        _die(f"无法移除补丁副本的 quarantine 属性: {output}")

    entitlements_path = _macos_adhoc_entitlements(app_path)
    try:
        rc, output = run_cmd([
            "codesign", "--force", "--sign", "-",
            "--preserve-metadata=identifier,flags,runtime",
            "--entitlements", entitlements_path,
            app_path,
        ], capture=True)
        if rc != 0:
            _die(f"macOS 签名失败: {output}")
    finally:
        if os.path.exists(entitlements_path):
            os.unlink(entitlements_path)

    rc, output = run_cmd([
        "codesign", "--verify", "--deep", "--strict", "--verbose=2", app_path,
    ], capture=True)
    if rc != 0:
        _die(f"macOS 签名验证失败: {output}")
    print("    签名完成并通过严格验证。")


def step_shortcut_windows(exe_path, work_dir):
    print("[7] 创建桌面快捷方式...")
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
        f"$lnk.Description='{display_name} (API Key 全功能解锁)';"
        f"$lnk.Save()"
    )
    run_cmd(["powershell", "-NoProfile", "-Command", ps])
    print(f"    已创建: {shortcut}")


# ================================================================
# 主流程
# ================================================================
print()
print("==========================================")
print("  ChatGPT Codex API Key 全功能解锁")
print("==========================================")
print()

if (args.app or args.output) and not IS_MACOS:
    _die("--app/--output 仅支持 macOS。")

if args.assets:
    # ── 仅重新打 JS 补丁（调试 / 重新适配新版本）──────────────────
    if args.app or args.output:
        _die("--assets 不能与 --app/--output 同时使用。")
    print(f"[手动模式] 仅执行 JS 补丁，目录: {args.assets}")
    if not os.path.isdir(args.assets):
        _die(f"目录不存在: {args.assets}")
    step_patch_js(args.assets)

else:
    # ── 完整流程 ────────────────────────────────────────────────
    source_root, resources_dir, exe_path, is_store = step_detect()
    macos_kill_paths = ()
    if IS_MACOS:
        macos_kill_paths = (
            source_root,
            _macos_patched_path(source_root, args.output),
        )
    step_kill_codex(exe_path, macos_kill_paths)

    if IS_MACOS:
        # 复制官方 app 到独立副本，补丁/签名只作用于副本，
        # 官方 app 保持 OpenAI 签名，作为 Appshots/原始 TCC 身份回退。
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
        print("[5] [DRY-RUN] 目标副本尚未创建，跳过 JS 内容检查")
    else:
        if not os.path.isdir(assets):
            _die(f"assets 目录不存在: {assets}")
        step_patch_js(assets)
        _require_successful_js_patch()

    # 关键: 将打好补丁的 app/ 重新打包回 app.asar
    # (owl 运行时只从 app.asar 加载，不支持 app/ 文件夹回退)
    step_repack_asar(resources_dir)

    if IS_MACOS:
        # 保持 Owl/Electron 的 ASAR 完整性元数据，不修改体积巨大的运行时框架。
        step_update_macos_asar_integrity(work_root)
        step_finish_macos(work_root)
    else:
        step_fuses(exe_path)
        if IS_WINDOWS and is_store:
            step_shortcut_windows(exe_path, work_root)

# ================================================================
# 汇总报告
# ================================================================
total = len(results["applied"]) + len(results["skipped"]) + len(results["failed"])
print()
print("=" * 50)
print("补丁报告")
print("=" * 50)
print(f"  总计 {total}  |  成功 {len(results['applied'])}  |"
      f"  跳过 {len(results['skipped'])}  |  失败 {len(results['failed'])}")

if results["applied"]:
    print("\n  已应用:")
    for r in results["applied"]:
        print(f"    + {r}")
if results["skipped"]:
    print("\n  已跳过 (已应用):")
    for r in results["skipped"]:
        print(f"    - {r}")
if results["failed"]:
    print("\n  失败 (需手动处理):")
    for r in results["failed"]:
        print(f"    x {r}")
    print("  -> 参考 SKILL.md 版本更新排查指南")
    sys.exit(1)

print()
if not args.assets and DRY_RUN:
    print("  预演完成：未关闭进程、复制文件、提取或重打包 app.asar。")
elif not args.assets:
    if IS_WINDOWS and is_store:
        shortcut_name = (
            "ChatGPT Codex (Patched)"
            if os.path.basename(exe_path).lower() == "chatgpt.exe"
            else "Codex (Patched)"
        )
        print(f"  补丁完成！通过桌面快捷方式 '{shortcut_name}' 启动。")
        print(f"  或直接运行: {exe_path}")
    elif IS_WINDOWS:
        print(f"  补丁完成！直接启动 {os.path.basename(exe_path)} 即可。")
    elif IS_MACOS:
        print(f"  补丁完成！启动打补丁的副本: {work_root}")
        print(f"  官方 {source_root} 保持不变，可用于原始签名/TCC 回退。")
        print("  Computer Use 权限项在系统设置中显示为 'Codex Computer Use'。")
print()
