#!/usr/bin/env python3
"""
Codex App — API Key 模式全功能解锁
跨平台一键脚本 (macOS / Windows)，路径全部自动检测，无需硬编码。

用法:
    python3 patch.py                        # 自动检测安装位置，执行完整流程
    python3 patch.py --assets /path/assets  # 仅对指定目录重新打 JS 补丁（跳过 asar/fuses）
    python3 patch.py --dry-run              # 预演（不写入任何文件）
"""

import argparse
import glob
import os
import re
import shutil
import subprocess
import sys
import time

# ================================================================
# 参数解析
# ================================================================
parser = argparse.ArgumentParser(description="Codex App API Key 模式全功能解锁")
parser.add_argument("--assets", metavar="DIR",
                    help="手动指定 webview/assets 目录，跳过 asar 解包 / fuses 步骤")
parser.add_argument("--dry-run", action="store_true",
                    help="预演模式：仅打印操作，不写入文件")
args = parser.parse_args()

IS_MACOS   = sys.platform == "darwin"
IS_WINDOWS = sys.platform == "win32"
DRY_RUN    = args.dry_run

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


# ================================================================
# 步骤 1: 关闭 Codex
# ================================================================
def step_kill_codex():
    print("[1] 关闭 Codex 进程...")
    if IS_MACOS:
        run_cmd(["pkill", "-x", "Codex"])
    elif IS_WINDOWS:
        run_cmd(["taskkill", "/F", "/IM", "Codex.exe"])
    time.sleep(1)


# ================================================================
# 步骤 2: 定位安装目录
# ================================================================
def step_detect():
    """
    返回 (source_root, resources_dir, exe_path, is_store)
    source_root  : 安装根目录（Store 版为只读 MSIX 目录）
    resources_dir: 含 app.asar 的可写 resources 目录
    exe_path     : 可执行文件路径（可写位置）
    is_store     : 是否为 Store 版（需先复制到可写目录）
    """
    print("[2] 定位 Codex 安装目录...")

    if IS_MACOS:
        app = "/Applications/Codex.app"
        if not os.path.isdir(app):
            _die("未找到 /Applications/Codex.app，请确认 Codex 已安装。")
        resources = os.path.join(app, "Contents", "Resources")
        exe       = os.path.join(app, "Contents", "MacOS", "Codex")
        print(f"  检测到 macOS 版: {app}")
        return app, resources, exe, False

    if IS_WINDOWS:
        local = os.environ.get("LOCALAPPDATA", "")
        if not local:
            _die("LOCALAPPDATA 环境变量未设置。")

        # ── 传统安装版 ──────────────────────────────────────
        trad_res = os.path.join(local, "Programs", "Codex", "resources")
        trad_exe = os.path.join(local, "Programs", "Codex", "Codex.exe")
        if os.path.isdir(trad_res):
            print(f"  检测到传统安装版: {os.path.join(local, 'Programs', 'Codex')}")
            return (os.path.join(local, "Programs", "Codex"),
                    trad_res, trad_exe, False)

        # ── Microsoft Store 版 (MSIX) ───────────────────────
        rc, store_root = run_cmd(
            ["powershell", "-NoProfile", "-Command",
             "Get-AppxPackage -Name 'OpenAI.Codex' | "
             "Select-Object -ExpandProperty InstallLocation"],
            capture=True
        )
        if store_root and os.path.isdir(store_root):
            print(f"  检测到 Store 版 (MSIX): {store_root}")
            return store_root, None, None, True   # resources/exe 在复制后确定

    _die("未找到 Codex 安装目录。请确认 Codex 已安装（Store 版或传统安装版）。")


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
    local      = os.environ["LOCALAPPDATA"]
    patch_root = os.path.join(local, "Programs", "Codex-Patched")
    resources  = os.path.join(patch_root, "resources")
    exe        = os.path.join(patch_root, "Codex.exe")
    src        = os.path.join(store_root, "app")

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

    if not os.path.isfile(asar) and not os.path.isfile(asar_bak):
        _die(f"未找到 app.asar: {asar}")

    if DRY_RUN:
        print("    [DRY-RUN] 跳过 asar 提取")
        return

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


def step_patch_js(assets):
    print(f"[5] 应用 JS 补丁...")
    print(f"    {assets}\n")

    # ── 模块 1: Fast 模式 / 服务层级 (1 补丁) ────────────────────
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
    for fp in files:
        # a=i?.authMethod===`chatgpt`,o=i?.authMethod??null  →  a=true||...
        # 保留 chatgpt 标记用于幂等 SKIP 检测
        apply_patch(fp, "服务层级授权门控",
            None, None,
            r'([a-zA-Z_$]+)=([a-zA-Z_$]+)\?\.authMethod===`chatgpt`,([a-zA-Z_$]+)=\2\?\.authMethod\?\?null',
            lambda m: f"{m.group(1)}=true||{m.group(2)}?.authMethod===`chatgpt`,{m.group(3)}={m.group(2)}?.authMethod??null",
            skip_regex=r'=true\|\|[a-zA-Z_$]+\?\.authMethod===`chatgpt`,[a-zA-Z_$]+=[a-zA-Z_$]+\?\.authMethod\?\?null')

    # ── 模块 2: i18n 多语言 (1 补丁) ────────────────────────────
    # 新版: app-main 中 React Compiler 形式 s=a?.get(`enable_i18n`,!1)
    # 旧版: r=(0,Q.useMemo)(()=>n?.get(`enable_i18n`,!1),[n])
    # 注: 旧版"插件侧边栏 (pluginsDisabledTooltip)"已被移除，
    #     插件门控现由模块 4 的 ge() 函数统一控制。
    print("\n  [模块 2] i18n 多语言")
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

    # ── 模块 3: 插件连接器 (1 补丁) ──────────────────────────────
    print("\n  [模块 3] 插件连接器")
    files = _find(assets, "check-plugin-availability-*.js")
    if not files:
        for f in glob.glob(os.path.join(assets, "*.js")):
            with open(f, encoding="utf-8") as fh:
                c = fh.read()
            if "connector-unavailable" in c:
                files = [f]; break
    for fp in files:
        apply_patch(fp, "插件连接器解锁",
            "(i=`connector-unavailable`)", "false&&(i=`connector-unavailable`)",
            r'(?<!&&)\(([a-zA-Z_$])=`connector-unavailable`\)',
            lambda m: f"false&&({m.group(1)}=`connector-unavailable`)",
            skip_regex=r'false&&\([a-zA-Z_$]=`connector-unavailable`\)')

    # ── 模块 4: 品牌视觉 + 插件门控 (1 补丁) ─────────────────────
    # 新版 (26.602+): use-plugins-*.js 中 function ge(e){return e!==`chatgpt`}
    #   该函数同时控制品牌视觉与插件侧边栏可用性。
    #   注意: 函数名(ge) 与参数名(e) 不再相同。
    # 旧版: plugin-auth-*.js / gradient-*.js
    print("\n  [模块 4] 品牌视觉 + 插件门控")
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

    # ── 模块 5: 语音输入 (1 补丁) ────────────────────────────────
    # 新版 (26.602+): use-is-dictation-supported-*.js 中 n&&t.authMethod===`chatgpt`
    # 旧版: annotation-comment-editor-card-*.js
    print("\n  [模块 5] 语音输入")
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

    # ── 模块 6: 用量设置 (1 补丁) ────────────────────────────────
    print("\n  [模块 6] 用量设置")
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
            ["npx", "@electron/fuses", "write", "--app", exe_path, flag],
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
# 步骤 7: 平台收尾
# ================================================================
def step_finish_macos(app_path):
    print("[7] 重新签名 (macOS)...")
    if not DRY_RUN:
        run_cmd(["codesign", "--force", "--deep", "--sign", "-", app_path])
    print("    签名完成。")


def step_shortcut_windows(exe_path, work_dir):
    print("[7] 创建桌面快捷方式...")
    desktop  = os.path.join(os.path.expanduser("~"), "Desktop")
    shortcut = os.path.join(desktop, "Codex (Patched).lnk")
    if DRY_RUN:
        print(f"    [DRY-RUN] {shortcut}")
        return
    ps = (
        f"$wsh=New-Object -ComObject WScript.Shell;"
        f"$lnk=$wsh.CreateShortcut('{shortcut}');"
        f"$lnk.TargetPath='{exe_path}';"
        f"$lnk.WorkingDirectory='{work_dir}';"
        f"$lnk.Description='Codex (API Key 全功能解锁)';"
        f"$lnk.Save()"
    )
    run_cmd(["powershell", "-NoProfile", "-Command", ps])
    print(f"    已创建: {shortcut}")


# ================================================================
# 主流程
# ================================================================
print()
print("==========================================")
print("  Codex API Key 全功能解锁")
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
    step_kill_codex()

    source_root, resources_dir, exe_path, is_store = step_detect()

    if IS_WINDOWS and is_store:
        patch_root, resources_dir, exe_path = step_copy_store(source_root)
        work_root = patch_root
    else:
        work_root = source_root

    step_extract_asar(resources_dir)

    assets = os.path.join(resources_dir, "app", "webview", "assets")
    if not os.path.isdir(assets) and not DRY_RUN:
        _die(f"assets 目录不存在: {assets}")
    step_patch_js(assets)

    # 关键: 将打好补丁的 app/ 重新打包回 app.asar
    # (owl 运行时只从 app.asar 加载，不支持 app/ 文件夹回退)
    step_repack_asar(resources_dir)

    step_fuses(exe_path)

    if IS_MACOS:
        step_finish_macos(source_root)
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
    if not results["applied"] and not results["skipped"]:
        sys.exit(1)

print()
if not args.assets:
    if IS_WINDOWS and is_store:
        print("  补丁完成！通过桌面快捷方式 'Codex (Patched)' 启动。")
        print(f"  或直接运行: {exe_path}")
    elif IS_WINDOWS:
        print("  补丁完成！直接启动 Codex 即可。")
    elif IS_MACOS:
        print("  补丁完成！启动 /Applications/Codex.app。")
print()
