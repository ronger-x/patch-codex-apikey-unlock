# Codex App - API Key 模式全功能解锁（Windows 一键补丁）
# 支持 Microsoft Store 版 (MSIX) 和传统安装版
# 使用方法: 右键 -> 以管理员身份运行，或在 PowerShell 中执行

Set-StrictMode -Off
$ErrorActionPreference = "Continue"

Write-Host ""
Write-Host "=========================================="
Write-Host "  Codex API Key 全功能解锁 — Windows 版"
Write-Host "=========================================="
Write-Host ""

# 关闭 Codex
Write-Host "[1/6] 关闭 Codex 进程..."
Stop-Process -Name "Codex" -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 1

# ── 自动定位安装目录 ──────────────────────────────────────────────
# 优先: 传统安装版
$traditionalResources = "$env:LOCALAPPDATA\Programs\Codex\resources"
$traditionalExe       = "$env:LOCALAPPDATA\Programs\Codex\Codex.exe"

# 其次: Store 版 (MSIX)
$storePkg = Get-AppxPackage -Name "OpenAI.Codex" -ErrorAction SilentlyContinue
$storeRoot = if ($storePkg) { $storePkg.InstallLocation } else { $null }

# 补丁工作目录（始终可写）
$patchRoot      = "$env:LOCALAPPDATA\Programs\Codex-Patched"
$patchResources = "$patchRoot\resources"
$patchExe       = "$patchRoot\Codex.exe"

if (Test-Path $traditionalResources) {
    # ── 传统安装版：原地打补丁 ────────────────────────────────────
    Write-Host "  检测到: 传统安装版"
    $codexResources = $traditionalResources
    $codexExe       = $traditionalExe
    $isStore        = $false
} elseif ($storeRoot -and (Test-Path "$storeRoot\app\resources\app.asar")) {
    # ── Store 版：复制到可写目录再打补丁 ─────────────────────────
    Write-Host "  检测到: Microsoft Store 版 (MSIX)"
    Write-Host "  Store 版受 MSIX 签名保护，将复制 app 目录到可写位置："
    Write-Host "    $patchRoot"
    $codexResources = $patchResources
    $codexExe       = $patchExe
    $isStore        = $true

    Write-Host ""
    Write-Host "[2/6] 复制 app 目录（约 300 MB，请稍候）..."
    if (Test-Path $patchRoot) {
        Remove-Item -Recurse -Force $patchRoot
    }
    New-Item -ItemType Directory -Force $patchRoot | Out-Null
    # /COPY:DAT 只复制数据/属性/时间戳，跳过 EFS 加密属性，避免"无法加密指定的文件"错误
    robocopy "$storeRoot\app" $patchRoot /E /COPY:DAT /NP /NFL /NDL /NJH /NJS | Out-Null
    if ($LASTEXITCODE -ge 8) {
        Write-Host "[ERROR] robocopy 复制失败 (exit $LASTEXITCODE)，请以管理员身份运行本脚本。"
        exit 1
    }
    Write-Host "  复制完成。"
} else {
    Write-Host "[ERROR] 未找到 Codex 安装目录。"
    Write-Host "        传统版路径: $traditionalResources"
    Write-Host "        Store 版:   $(if ($storeRoot) { $storeRoot } else { '未检测到 OpenAI.Codex 包' })"
    exit 1
}

if (-not $isStore) {
    Write-Host "[2/6] 跳过复制（传统安装版原地操作）"
}

Set-Location $codexResources

# 备份 app.asar（仅首次）
Write-Host "[3/6] 备份 app.asar..."
if (-not (Test-Path app.asar.bak)) {
    Copy-Item app.asar app.asar.bak
    Write-Host "      已备份 app.asar -> app.asar.bak"
} else {
    Write-Host "      备份已存在，跳过。"
}

# 清理旧补丁目录
Remove-Item -Recurse -Force app -ErrorAction SilentlyContinue

# 提取 asar
Write-Host "[4/6] 提取 app.asar..."
npx --yes @electron/asar e ./app.asar app
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] asar 提取失败，请确认 Node.js 已安装。"
    exit 1
}

# 重命名 asar（Electron 会自动加载 app/ 文件夹）
if (Test-Path app.asar) {
    Rename-Item app.asar app.asar1
}

# 执行 Python 补丁脚本
Write-Host "[5/6] 执行 Python 补丁..."
$pythonScript = @'
import os, glob, re, sys

# ================================================================
# 配置 — 路径由 PowerShell 通过环境变量传入
# ================================================================
BASE = os.environ.get("CODEX_ASSETS_BASE", os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Codex", "resources", "app", "webview", "assets"))

results = {"applied": [], "skipped": [], "failed": []}

def find_file(pattern):
    matches = glob.glob(os.path.join(BASE, pattern))
    return matches

def apply_patch(filepath, name, find_str, replace_str, find_regex=None, replace_fn=None):
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    basename = os.path.basename(filepath)

    if replace_str and replace_str in content:
        results["skipped"].append(f"{basename}: {name} (已应用)")
        print(f"  [SKIP] {name} — 已应用")
        return content

    if find_str and find_str in content:
        content = content.replace(find_str, replace_str, 1)
        results["applied"].append(f"{basename}: {name}")
        print(f"  [OK]   {name}")
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        return content

    if find_regex and replace_fn:
        m = re.search(find_regex, content)
        if m:
            old_text = m.group(0)
            new_text = replace_fn(m)
            if old_text != new_text:
                content = content.replace(old_text, new_text, 1)
                results["applied"].append(f"{basename}: {name} (regex)")
                print(f"  [OK]   {name} (regex匹配)")
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(content)
                return content

    results["failed"].append(f"{basename}: {name}")
    print(f"  [FAIL] {name} — 未找到匹配模式")
    return None


# ================================================================
# 模块 1: Fast 模式 (3 个补丁)
# 新版: use-is-fast-mode-enabled-*.js
# 旧版: permissions-mode-helpers-*.js
# ================================================================
print("\n[模块 1] Fast 模式")

# 优先搜索新版文件名
files = find_file("use-is-fast-mode-enabled-*.js")
if files:
    print(f"  发现新版: {os.path.basename(files[0])}")
else:
    # 尝试旧版文件名
    files = find_file("permissions-mode-helpers-*.js")
    if files:
        # 确认文件内确实包含目标模式
        with open(files[0], "r", encoding="utf-8") as fh:
            _c = fh.read()
        if "authMethod" not in _c:
            files = []  # 文件存在但没有目标内容，继续搜索
    if not files:
        print("  未找到已知文件名，搜索所有 JS...")
        for f in glob.glob(os.path.join(BASE, "*.js")):
            with open(f, "r", encoding="utf-8") as fh:
                c = fh.read()
            if "authMethod" in c and "models.some" in c:
                files = [f]
                print(f"  -> 发现目标: {os.path.basename(f)}")
                break

for filepath in files:
    apply_patch(filepath,
        name="Fast 授权门控",
        find_str="return!(r?.authMethod!==`chatgpt`||a)",
        replace_str="return true",
        find_regex=r'return!\([a-zA-Z_$]+\?\.authMethod!==`chatgpt`\|\|[a-zA-Z_$]+\)',
        replace_fn=lambda m: "return true"
    )
    apply_patch(filepath,
        name="Fast Hook 早期返回",
        find_str="if(i?.authMethod!==`chatgpt`||s){",
        replace_str="if(false&&i?.authMethod!==`chatgpt`||s){",
        find_regex=r'if\(([a-zA-Z_$]+)\?\.authMethod!==`chatgpt`\|\|([a-zA-Z_$]+)\)\{',
        replace_fn=lambda m: f"if(false&&{m.group(1)}?.authMethod!==`chatgpt`||{m.group(2)}){{"
    )
    # 注意: replace_str 不能用 "true" (JS 中太常见会误触发 SKIP 检测)
    # 用带赋值的完整表达式作为 find_str，replace_str 也带赋值上下文
    apply_patch(filepath,
        name="模型可用性检查",
        find_str="b=v?.models.some(m)??!1",
        replace_str="b=true",
        find_regex=r'([a-zA-Z_$])=([a-zA-Z_$]+)\.models\.some\([a-zA-Z_$]+\)\?\?!1',
        replace_fn=lambda m: f"{m.group(1)}=true"
    )


# ================================================================
# 模块 2: app-main — 插件侧边栏 + i18n (2 个补丁)
# ================================================================
print("\n[模块 2] 插件侧边栏 + i18n — app-main-*.js")

files = find_file("app-main-*.js")
if not files:
    print("  未找到 app-main-*.js，搜索所有 JS...")
    for f in glob.glob(os.path.join(BASE, "*.js")):
        with open(f, "r", encoding="utf-8") as fh:
            c = fh.read()
        if "pluginsDisabledTooltip" in c and "enable_i18n" in c:
            files = [f]
            print(f"  -> 发现目标: {os.path.basename(f)}")
            break

for filepath in files:
    apply_patch(filepath,
        name="插件侧边栏解锁",
        find_str="d?(0,$.jsx)(rf,{tooltipContent:(0,$.jsx)(Y,{id:`sidebarElectron.pluginsDisabledTooltip`",
        replace_str="0?(0,$.jsx)(rf,{tooltipContent:(0,$.jsx)(Y,{id:`sidebarElectron.pluginsDisabledTooltip`",
        find_regex=r'([a-zA-Z_$])\?\(0,\$\.jsx\)\([a-zA-Z_$]+,\{tooltipContent:\(0,\$\.jsx\)\([a-zA-Z_$]+,\{id:`sidebarElectron\.pluginsDisabledTooltip`',
        replace_fn=lambda m: m.group(0).replace(m.group(1) + "?", "0?", 1)
    )
    apply_patch(filepath,
        name="i18n 多语言强制启用",
        find_str="r=(0,Q.useMemo)(()=>n?.get(`enable_i18n`,!1),[n])",
        replace_str="r=(0,Q.useMemo)(()=>!0,[n])",
        find_regex=r'([a-zA-Z_$])=\(0,[a-zA-Z_$]+\.useMemo\)\(\(\)=>[a-zA-Z_$]+\?\.get\(`enable_i18n`,!1\),\[[a-zA-Z_$]+\]\)',
        replace_fn=lambda m: f"{m.group(1)}=(0,Q.useMemo)(()=>!0,[n])"
    )


# ================================================================
# 模块 3: check-plugin-availability — 插件连接器 (1 个补丁)
# ================================================================
print("\n[模块 3] 插件连接器 — check-plugin-availability-*.js")

files = find_file("check-plugin-availability-*.js")
if not files:
    print("  未找到 check-plugin-availability-*.js，搜索所有 JS...")
    for f in glob.glob(os.path.join(BASE, "*.js")):
        with open(f, "r", encoding="utf-8") as fh:
            c = fh.read()
        if "connector-unavailable" in c:
            files = [f]
            print(f"  -> 发现目标: {os.path.basename(f)}")
            break

for filepath in files:
    apply_patch(filepath,
        name="插件连接器解锁",
        find_str="(i=`connector-unavailable`)",
        replace_str="false&&(i=`connector-unavailable`)",
        find_regex=r'\(([a-zA-Z_$])=`connector-unavailable`\)',
        replace_fn=lambda m: f"false&&({m.group(1)}=`connector-unavailable`)"
    )


# ================================================================
# 模块 4: 品牌视觉 (1 个补丁)
# 新版: plugin-auth-*.js
# 旧版: gradient-*.js
# ================================================================
print("\n[模块 4] 品牌视觉")

# 优先搜索新版文件名
files = find_file("plugin-auth-*.js")
if files:
    print(f"  发现新版: {os.path.basename(files[0])}")
else:
    # 尝试旧版文件名
    files = find_file("gradient-*.js")
    if files:
        with open(files[0], "r", encoding="utf-8") as fh:
            _c = fh.read()
        if "chatgpt" not in _c:
            files = []
    if not files:
        print("  未找到已知文件名，搜索所有 JS...")
        for f in glob.glob(os.path.join(BASE, "*.js")):
            with open(f, "r", encoding="utf-8") as fh:
                c = fh.read()
            if "function e(e){return e!==`chatgpt`}" in c:
                files = [f]
                print(f"  -> 发现目标: {os.path.basename(f)}")
                break

for filepath in files:
    apply_patch(filepath,
        name="品牌视觉统一",
        find_str="function e(e){return e!==`chatgpt`}",
        replace_str="function e(e){return false}",
        find_regex=r'function\s+([a-zA-Z_$]+)\(\1\)\{return\s+\1!==`chatgpt`\}',
        replace_fn=lambda m: f"function {m.group(1)}({m.group(1)}){{return false}}"
    )


# ================================================================
# 模块 5: annotation-comment-editor-card — 语音输入 (1 个补丁)
# ================================================================
print("\n[模块 5] 语音输入 — annotation-comment-editor-card-*.js")

files = find_file("annotation-comment-editor-card-*.js")
if not files:
    print("  未找到 annotation-comment-editor-card-*.js，搜索所有 JS...")
    for f in glob.glob(os.path.join(BASE, "*.js")):
        with open(f, "r", encoding="utf-8") as fh:
            c = fh.read()
        if "authMethod===`chatgpt`" in c and "dictation" in c.lower():
            files = [f]
            print(f"  -> 发现目标: {os.path.basename(f)}")
            break

for filepath in files:
    apply_patch(filepath,
        name="语音输入解锁",
        find_str="n&&t.authMethod===`chatgpt`",
        replace_str="n&&(t.authMethod===`chatgpt`||t.authMethod===`apikey`)",
        find_regex=r'([a-zA-Z_$]+)&&([a-zA-Z_$]+)\.authMethod===`chatgpt`',
        replace_fn=lambda m: f"{m.group(1)}&&({m.group(2)}.authMethod===`chatgpt`||{m.group(2)}.authMethod===`apikey`)"
    )


# ================================================================
# 模块 6: use-usage-settings-access — 用量设置 (1 个补丁)
# ================================================================
print("\n[模块 6] 用量设置 — use-usage-settings-access-*.js")

files = find_file("use-usage-settings-access-*.js")
if not files:
    print("  未找到 use-usage-settings-access-*.js，搜索所有 JS...")
    for f in glob.glob(os.path.join(BASE, "*.js")):
        with open(f, "r", encoding="utf-8") as fh:
            c = fh.read()
        if "let r=e===`chatgpt`" in c:
            files = [f]
            print(f"  -> 发现目标: {os.path.basename(f)}")
            break

for filepath in files:
    apply_patch(filepath,
        name="用量设置解锁",
        find_str="let r=e===`chatgpt`",
        replace_str="let r=e===`chatgpt`||e===`apikey`",
        find_regex=r'let\s+([a-zA-Z_$]+)=([a-zA-Z_$]+)===`chatgpt`',
        replace_fn=lambda m: f"let {m.group(1)}={m.group(2)}===`chatgpt`||{m.group(2)}===`apikey`"
    )


# ================================================================
# 汇总报告
# ================================================================
print("\n" + "=" * 60)
print("补丁报告")
print("=" * 60)
total = len(results["applied"]) + len(results["skipped"]) + len(results["failed"])
print(f"  总计: {total} 个补丁")
print(f"  成功: {len(results['applied'])} 个")
print(f"  跳过: {len(results['skipped'])} 个（已应用）")
print(f"  失败: {len(results['failed'])} 个")

if results["applied"]:
    print("\n  已应用:")
    for r in results["applied"]:
        print(f"    v {r}")

if results["skipped"]:
    print("\n  已跳过:")
    for r in results["skipped"]:
        print(f"    - {r}")

if results["failed"]:
    print("\n  失败（需手动处理）:")
    for r in results["failed"]:
        print(f"    x {r}")

if results["failed"] and not results["applied"] and not results["skipped"]:
    print("\n[ERROR] 所有补丁均失败！可能是全新版本，请参考 SKILL.md 排查指南。")
    sys.exit(1)

print("\n补丁脚本执行完毕。")
'@

# 传入 assets 目录给 Python
$env:CODEX_ASSETS_BASE = "$codexResources\app\webview\assets"
$pythonScript | python3
Remove-Item Env:CODEX_ASSETS_BASE -ErrorAction SilentlyContinue
if ($LASTEXITCODE -ne 0) {
    Write-Host "[WARN] Python 补丁脚本返回非零退出码，部分补丁可能失败。"
}

# 禁用 Electron fuses
Write-Host ""
Write-Host "[6/6] 禁用 Electron fuses..."
npx @electron/fuses write --app $codexExe OnlyLoadAppFromAsar=off
npx @electron/fuses write --app $codexExe EnableEmbeddedAsarIntegrityValidation=off
npx @electron/fuses write --app $codexExe GrantFileProtocolExtraPrivileges=off
npx @electron/fuses write --app $codexExe EnableCookieEncryption=off

Write-Host ""
Write-Host "=========================================="
Write-Host "  Codex API Key 全功能解锁 — 补丁完成"
Write-Host "=========================================="
Write-Host ""

if ($isStore) {
    Write-Host "  Store 版已将 app 复制到:"
    Write-Host "    $patchRoot"
    Write-Host ""
    Write-Host "  请通过以下命令启动补丁后的 Codex："
    Write-Host "    & `"$patchExe`""
    Write-Host ""
    # 创建桌面快捷方式
    $desktop = [Environment]::GetFolderPath('Desktop')
    $shortcut = "$desktop\Codex (Patched).lnk"
    $wsh = New-Object -ComObject WScript.Shell
    $lnk = $wsh.CreateShortcut($shortcut)
    $lnk.TargetPath = $patchExe
    $lnk.WorkingDirectory = $patchRoot
    $lnk.Description = "Codex (API Key 全功能解锁)"
    $lnk.Save()
    Write-Host "  已在桌面创建快捷方式: Codex (Patched).lnk"
} else {
    Write-Host "  启动 Codex，使用 API key 模式登录即可。"
}

Write-Host ""
Write-Host "  如需回滚："
if ($isStore) {
    Write-Host "    Remove-Item -Recurse -Force `"$patchRoot`""
    Write-Host "    (直接删除补丁目录，原 Store 版未动)"
} else {
    Write-Host "    cd `"$codexResources`""
    Write-Host "    Remove-Item -Recurse -Force app"
    Write-Host "    if (Test-Path app.asar1) { Rename-Item app.asar1 app.asar }"
    Write-Host "    if (Test-Path app.asar.bak) { Copy-Item app.asar.bak app.asar }"
}
Write-Host ""
