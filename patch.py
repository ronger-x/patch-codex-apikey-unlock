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
import os
import plistlib
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

# ================================================================
# 参数解析
# ================================================================
parser = argparse.ArgumentParser(description="ChatGPT Codex API Key 模式全功能解锁")
parser.add_argument("--assets", metavar="DIR",
                    help="手动指定 webview/assets 目录，跳过 asar 解包 / fuses 步骤")
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
MACOS_APP_PATHS = ("/Applications/ChatGPT.app", "/Applications/Codex.app")
MACOS_EXE_NAMES = ("ChatGPT", "Codex")

if DRY_RUN:
    print("[DRY-RUN] 预演模式，不会实际修改文件\n")


# ================================================================
# 工具：运行子进程
# ================================================================
def run_cmd(cmd, capture=False):
    """执行命令，返回 (returncode, stdout_str)。"""
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
    stdout = result.stdout.strip() if capture and result.stdout else ""
    return result.returncode, stdout


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
def step_kill_codex(exe_path):
    print("[2] 关闭 ChatGPT Codex 进程...")
    if DRY_RUN:
        print("    [DRY-RUN] 跳过关闭进程")
        return
    process_name = os.path.basename(exe_path)
    if IS_MACOS:
        run_cmd(["pkill", "-x", process_name])
    elif IS_WINDOWS:
        run_cmd(["taskkill", "/F", "/IM", process_name])
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
        for app in MACOS_APP_PATHS:
            resources = os.path.join(app, "Contents", "Resources")
            exe = _macos_executable(app) if os.path.isdir(app) else None
            if exe is None or not _is_codex_resources(resources):
                continue
            print(f"  检测到 macOS 版: {app} ({os.path.basename(exe)})")
            return app, resources, exe, False
        else:
            _die("未找到 /Applications/ChatGPT.app 或 /Applications/Codex.app。")

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
        shutil.rmtree(patch_root)
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

    rc, _ = run_cmd(["npx", "--yes", "@electron/asar", "e", asar, app_dir])
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
        "npx", "--yes", "@electron/asar", "pack", app_dir, asar,
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
    for fp in _find(assets, "use-plugins-*.js"):
        with open(fp, encoding="utf-8") as fh:
            if pattern.search(fh.read()):
                return fp
    return None


def apply_model_filter_patch(fp):
    with open(fp, encoding="utf-8") as fh:
        content = fh.read()
    bn = os.path.basename(fp)

    auth_match = re.search(
        r'function [a-zA-Z_$]+\(\{[^}]*\bauthMethod'
        r'(?::(?P<auth>[a-zA-Z_$]+))?(?:,|\})',
        content,
    )
    condition_pattern = re.compile(
        r'if\((?P<mode>[a-zA-Z_$]+)\?'
        r'(?P<allowed>[a-zA-Z_$]+)\.has\('
        r'(?P<model>[a-zA-Z_$]+)\.model\):!'
        r'(?P=model)\.hidden\)\{'
    )
    if auth_match is None or "useHiddenModels" not in auth_match.group(0):
        # authMethod may appear before useHiddenModels; validate against the full signature.
        signature_match = re.search(
            r'function [a-zA-Z_$]+\(\{(?P<fields>[^}]*)\}\)\{', content)
        if signature_match is None or "useHiddenModels" not in signature_match.group("fields"):
            mark_missing(f"{bn}: 隐藏模型列表解锁", "目标结构不匹配")
            return
    if auth_match is None:
        mark_missing(f"{bn}: 隐藏模型列表解锁", "目标结构不匹配")
        return

    auth = auth_match.group("auth") or "authMethod"
    patched_pattern = re.compile(
        rf'if\({re.escape(auth)}===`apikey`\|\|\('
        r'[a-zA-Z_$]+\?[a-zA-Z_$]+\.has\('
        r'([a-zA-Z_$]+)\.model\):!\1\.hidden\)\)\{'
    )
    if patched_pattern.search(content):
        results["skipped"].append(f"{bn}: 隐藏模型列表解锁")
        print("    [SKIP] 隐藏模型列表解锁")
        return

    condition_match = condition_pattern.search(content)
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
        content = content.replace(condition_match.group(0), patched, 1)
        with open(fp, "w", encoding="utf-8") as fh:
            fh.write(content)
    results["applied"].append(f"{bn}: 隐藏模型列表解锁 (regex)")
    print("    [OK]   隐藏模型列表解锁 (regex)")


def step_patch_js(assets):
    print(f"[5] 应用 JS 补丁...")
    print(f"    {assets}\n")

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

    # ── 模块 2: 最新模型 / 隐藏模型列表 (1 补丁) ────────────────
    # API key 模式没有 ChatGPT Statsig 的 hidden-model 白名单。
    # list-models-for-host 已请求 includeHidden=true，这里放开默认分支即可展示
    # 后端实际返回的新模型，同时保留 ChatGPT 账号模式的显式白名单逻辑。
    print("\n  [模块 2] 最新模型 / 隐藏模型列表")
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
            apply_model_filter_patch(model_filter_fp)
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
    files = _find(assets, "app-main-*.js")
    if not files:
        for f in glob.glob(os.path.join(assets, "*.js")):
            with open(f, encoding="utf-8") as fh:
                if "enable_i18n" in fh.read():
                    files = [f]; break
    for fp in files:
        apply_patch(fp, "i18n 多语言强制启用",
            None, None,
            r'([a-zA-Z_$]+)=([a-zA-Z_$]+)\?\.get\(`enable_i18n`,!1\)',
            lambda m: f"{m.group(1)}=true||{m.group(2)}?.get(`enable_i18n`,!1)",
            skip_regex=r'=true\|\|[a-zA-Z_$]+\?\.get\(`enable_i18n`,!1\)')

    # ── 模块 4: 旧版插件连接器 UI 门控 (1 补丁) ─────────────────
    print("\n  [模块 4] 旧版插件连接器 UI 门控")
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

    # ── 模块 5: 品牌视觉 + 插件市场门控 (1 补丁) ────────────────
    # 新版 (26.602+): use-plugins-*.js 中 function ge(e){return e!==`chatgpt`}
    #   该函数同时控制品牌视觉与插件侧边栏可用性。
    #   注意: 函数名(ge) 与参数名(e) 不再相同。
    # 旧版: plugin-auth-*.js / gradient-*.js
    print("\n  [模块 5] 品牌视觉 + 插件市场门控")
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

    # ── 模块 6: 语音输入 (1 补丁) ────────────────────────────────
    # 新版 (26.602+): use-is-dictation-supported-*.js 中 n&&t.authMethod===`chatgpt`
    # 旧版: annotation-comment-editor-card-*.js
    print("\n  [模块 6] 语音输入")
    files = _find(assets, "use-is-dictation-supported-*.js")
    if not files:
        files = _find(assets, "annotation-comment-editor-card-*.js")
    if not files:
        # 精确匹配：含 dictation 判定模式的文件，避免误选 app-main
        for f in glob.glob(os.path.join(assets, "*.js")):
            with open(f, encoding="utf-8") as fh:
                c = fh.read()
            if "dictation" in c.lower() and re.search(
                    r'[a-zA-Z_$]+&&[a-zA-Z_$]+\.authMethod===`chatgpt`', c):
                files = [f]; break
    for fp in files:
        apply_patch(fp, "语音输入解锁",
            None, None,
            r'([a-zA-Z_$]+)&&([a-zA-Z_$]+)\.authMethod===`chatgpt`(?!\|\|)',
            lambda m: f"{m.group(1)}&&({m.group(2)}.authMethod===`chatgpt`||{m.group(2)}.authMethod===`apikey`)",
            skip_regex=r'authMethod===`chatgpt`\|\|[a-zA-Z_$]+\.authMethod===`apikey`')

    # ── 模块 7: 用量设置 (1 补丁) ────────────────────────────────
    print("\n  [模块 7] 用量设置")
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
            ["npx", "--yes", "@electron/fuses", "write", "--app", exe_path, flag],
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
# 说明: macOS TCC 权限(屏幕录制/辅助功能/自动化)绑定到代码的签名身份
# (Designated Requirement)，而非 bundle id。若直接修改官方 app
# 并 ad-hoc 重签名，会让系统把它当成另一个 app，导致 Appshots/Computer Use
# 依赖的权限失效(见 GitHub issue #1)。
# 因此改为: 官方 app 原样保留(继续供 Appshots/Computer Use 使用)，
# 补丁只打到独立副本上，副本仅用于 API key 解锁功能。


def step_copy_macos(official_app):
    """
    将官方 ChatGPT/Codex app 复制到独立 Patched 副本
    （保留所有属性/符号链接/扩展属性）。
    返回 (patched_app, resources_dir, exe_path)
    """
    source_exe = _macos_executable(official_app)
    if source_exe is None:
        _die(f"未找到 ChatGPT/Codex 可执行文件: {official_app}")
    exe_name = os.path.basename(source_exe)
    patched_name = (
        "ChatGPT-Codex-Patched.app"
        if exe_name == "ChatGPT"
        else "Codex-Patched.app"
    )
    patched_app = os.path.join("/Applications", patched_name)
    resources   = os.path.join(patched_app, "Contents", "Resources")
    exe         = os.path.join(patched_app, "Contents", "MacOS", exe_name)

    print("[3] 复制官方 app 到独立副本...")
    print(f"    {official_app}")
    print(f"    -> {patched_app}")
    print(f"    (官方 {official_app} 保持不变，Appshots/Computer Use 不受影响)")

    if DRY_RUN:
        print("    [DRY-RUN] 跳过复制")
        return patched_app, resources, exe

    if os.path.exists(patched_app):
        shutil.rmtree(patched_app)

    # ditto 完整保留 bundle 结构、符号链接与扩展属性，得到与官方一致的初始副本
    rc, _ = run_cmd(["ditto", official_app, patched_app])
    if rc != 0:
        _die("复制 Codex.app 失败，请确认有写入 /Applications 的权限。")

    print("    复制完成。")
    return patched_app, resources, exe


# ================================================================
# 步骤 7: 平台收尾
# ================================================================
def step_finish_macos(app_path):
    # 仅对 patched 副本做 ad-hoc 签名(修改 app.asar 后原签名失效，需重签才能启动)。
    # 副本只用于 fast/plugins，不需要 TCC 权限，ad-hoc 足够；官方 app 不在此处理。
    print("[7] 重新签名 (macOS 副本)...")
    if not DRY_RUN:
        run_cmd(["codesign", "--force", "--deep", "--sign", "-", app_path])
    print("    签名完成。")


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

if args.assets:
    # ── 仅重新打 JS 补丁（调试 / 重新适配新版本）──────────────────
    print(f"[手动模式] 仅执行 JS 补丁，目录: {args.assets}")
    if not os.path.isdir(args.assets):
        _die(f"目录不存在: {args.assets}")
    step_patch_js(args.assets)

else:
    # ── 完整流程 ────────────────────────────────────────────────
    source_root, resources_dir, exe_path, is_store = step_detect()
    step_kill_codex(exe_path)

    if IS_MACOS:
        # 复制官方 app 到独立副本，补丁/签名只作用于副本，
        # 官方 app 保持 OpenAI 签名供 Appshots/Computer Use 使用。
        work_root, resources_dir, exe_path = step_copy_macos(source_root)
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

    # 关键: 将打好补丁的 app/ 重新打包回 app.asar
    # (owl 运行时只从 app.asar 加载，不支持 app/ 文件夹回退)
    step_repack_asar(resources_dir)

    step_fuses(exe_path)

    if IS_MACOS:
        step_finish_macos(work_root)
    elif IS_WINDOWS and is_store:
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
        print(f"  官方 {source_root} 保持不变，Appshots/Computer Use 仍可正常使用。")
        print("  (副本用于 API key 功能，官方版用于 Appshots/Computer Use)")
print()
