---
name: patch-codex-apikey-unlock
description: |
  Patch Codex App (macOS/Windows) - API Key 模式全功能解锁。
  使 API key 模式拥有与 ChatGPT 账号模式完全相同的功能，包括：
  Fast/Speed 模式、Plugins 插件、语音输入、用量设置、多语言 i18n、品牌视觉。
  支持版本自动发现，当 Codex 更新后文件名 hash 变化时自动定位目标文件。
version: 3.0.0
---

# Patch Codex App - API Key 模式全功能解锁

## 方案概述

放弃 ChatGPT 账号登录模式，使用 API key 模式并解锁全部功能。
无需配置代理、无需处理 OAuth 路由，只需一次补丁即可获得完整体验。

### 解锁功能清单

| # | 功能 | 原始限制 | 补丁方式 |
|---|------|----------|----------|
| 1 | Fast/Speed 模式授权 | `authMethod !== 'chatgpt'` 时隐藏 | 强制返回 `true` |
| 2 | Fast 模式 Hook 分支 | Hook 提前退出阻止渲染 | `false&&` 禁用条件 |
| 3 | 模型可用性检查 | relay API 缺少 `additionalSpeedTiers` 字段 | 强制返回 `true` |
| 4 | Plugins 侧边栏 | 非 chatgpt 模式禁用 | 门控变量 → `0` |
| 5 | 插件连接器可用性 | API key 模式标记为 `connector-unavailable` | `false&&` 禁用 |
| 6 | 品牌视觉统一 | API key 用户显示不同品牌 | 强制返回 `false` |
| 7 | 语音输入/听写 | 仅 chatgpt 模式 | 扩展为 `chatgpt \|\| apikey` |
| 8 | 用量/计费设置 | 仅 chatgpt 模式 | 扩展为 `chatgpt \|\| apikey` |
| 9 | i18n 多语言 | Statsig 实验门控，API key 无用户上下文时默认关闭 | 强制启用 `!0` |

## 前置要求

- Python 3
- Node.js（用于 `npx @electron/asar` 和 `@electron/fuses`）

## 一键使用（推荐）

```bash
# macOS / Windows，路径自动检测
python3 patch.py

# 仅重新打 JS 补丁（Codex 更新后重新适配，跳过 asar/fuses 步骤）
python3 patch.py --assets /path/to/webview/assets

# 预演，不写入任何文件
python3 patch.py --dry-run
```

`patch.py` 自动完成所有步骤：关闭进程 → 检测安装类型 → 复制（Store 版）→ 提取 asar → 打 JS 补丁 → 禁用 Electron fuses → 创建快捷方式（Windows Store）/ 重签名（macOS）。

## macOS

### 回滚方法

```bash
cd /Applications/Codex.app/Contents/Resources
rm -rf app
[ -f app.asar1 ] && mv app.asar1 app.asar
[ -f app.asar.bak ] && cp app.asar.bak app.asar
codesign --force --deep --sign - /Applications/Codex.app
echo "已回滚到原始版本"
```

### 一键补丁（macOS）

```bash
python3 patch.py
```

内部执行步骤（供参考）：

```bash
# Step 1: 关闭进程
pkill -x Codex 2>/dev/null; sleep 1

# Step 2: 提取 asar
cd /Applications/Codex.app/Contents/Resources
[ ! -f app.asar.bak ] && cp app.asar app.asar.bak
npx @electron/asar e ./app.asar app
mv ./app.asar ./app.asar1

# Step 3: 执行 JS 补丁（patch.py 内部逻辑，见 patch.py 源码）

# Step 4: 禁用 Electron fuses (patch.py 内部调用)
npx @electron/fuses write --app /Applications/Codex.app OnlyLoadAppFromAsar=off
npx @electron/fuses write --app /Applications/Codex.app EnableEmbeddedAsarIntegrityValidation=off
npx @electron/fuses write --app /Applications/Codex.app GrantFileProtocolExtraPrivileges=off
npx @electron/fuses write --app /Applications/Codex.app EnableCookieEncryption=off

# Step 5: 重新签名 (patch.py 内部调用)
codesign --force --deep --sign - /Applications/Codex.app
```

> 以上步骤均由 `python3 patch.py` 自动完成，无需手动执行。


---

## Windows

### 回滚方法

**Store 版（MSIX，Codex-Patched 目录）：**
```powershell
# 直接删除补丁目录，原 Store 版未动
Remove-Item -Recurse -Force "$env:LOCALAPPDATA\Programs\Codex-Patched"
```

**传统安装版：**
```powershell
cd "$env:LOCALAPPDATA\Programs\Codex\resources"
Remove-Item -Recurse -Force app -ErrorAction SilentlyContinue
if (Test-Path app.asar1) { Rename-Item app.asar1 app.asar }
if (Test-Path app.asar.bak) { Copy-Item app.asar.bak app.asar }
Write-Host "已回滚到原始版本"
```

### 一键补丁（Windows）

```powershell
python3 patch.py
```

> 自动检测 Microsoft Store 版（MSIX）和传统安装版。
> Store 版会复制 app 目录到 `%LOCALAPPDATA%\Programs\Codex-Patched`（原版不动），并在桌面创建 `Codex (Patched)` 快捷方式。

---

## config.toml 参考配置

补丁完成后，编辑 `~/.codex/config.toml` 配置你的 API provider：

```toml
model_provider = "openai"
base_url = "https://your-api-provider.com/v1"
experimental_bearer_token = "sk-your-api-key"

[features]
enable_fast = true
enable_speed_128k = true
enable_pro = true
enable_o3_pro = true
enable_deep_research = true
enable_codex_cloud = true
```

---

## 版本更新排查指南

Codex 更新后 JS 文件名（hash 后缀）和变量名都可能变化。以下是每个补丁的定位方法：

### 通用搜索策略

```bash
# 用 --assets 参数只重新打补丁，跳过 asar/fuses 步骤
python3 patch.py --assets /Applications/Codex.app/Contents/Resources/app/webview/assets
# Windows (Store 版)
python3 patch.py --assets "$env:LOCALAPPDATA\Programs\Codex-Patched\resources\app\webview\assets"

# 手动搜索（macOS）
cd /Applications/Codex.app/Contents/Resources/app/webview/assets
```

### 1. Fast 模式授权门控

```bash
# 特征关键词: authMethod + chatgpt + return
grep -rn "authMethod" *.js | grep "chatgpt" | grep "return"
# 找到包含 return!(xxx?.authMethod!==`chatgpt`||yyy) 的行
# 替换整个 return 表达式为: return true
```

### 2. Fast 模式 Hook 早期返回

```bash
# 在同一个文件中，找 if(xxx?.authMethod!==`chatgpt`||yyy){
grep -o ".{0,30}authMethod!==.chatgpt.{0,20}" <文件名>
# 在 if 条件前加 false&& 使其永远不进入
```

### 3. 模型可用性检查

```bash
# 在同一个文件中，找 xxx?.models.some(YYY)??!1
grep -o ".{0,20}models\.some.{0,30}" <文件名>
# 替换整个表达式为: true
```

### 4. 插件侧边栏

```bash
grep -rl "pluginsDisabledTooltip" *.js
# 找到 X?(0,$.jsx)(组件,{tooltipContent... 中的门控变量 X
# 将 X? 改为 0?
```

### 5. 插件连接器

```bash
grep -rl "connector-unavailable" *.js
# 找到 (变量=`connector-unavailable`)
# 前面加 false&& 使其永远不执行
```

### 6. 品牌视觉

```bash
grep -rn "return e!==.chatgpt." *.js
# 找到 function e(e){return e!==`chatgpt`}
# 改为 function e(e){return false}
```

### 7. 语音输入

```bash
grep -rn "authMethod===.chatgpt." *.js | grep -v "!=="
# 找到 xxx&&yyy.authMethod===`chatgpt` 且在 annotation/comment/editor 相关文件中
# 扩展为 xxx&&(yyy.authMethod===`chatgpt`||yyy.authMethod===`apikey`)
```

### 8. 用量设置

```bash
grep -rn "let.*===.chatgpt." *.js
# 找到 let r=e===`chatgpt` 且在 usage-settings 相关文件中
# 扩展为 let r=e===`chatgpt`||e===`apikey`
```

### 9. i18n 多语言

```bash
grep -rn "enable_i18n" *.js
# 找到 xxx=(0,YYY.useMemo)(()=>nnn?.get(`enable_i18n`,!1),[nnn])
# 改为 xxx=(0,YYY.useMemo)(()=>!0,[nnn])
# 关键: Statsig 实验门控在无用户上下文时默认返回 false
```

## 原理说明

| 操作 | 原因 | 位置 |
|------|------|------|
| `OnlyLoadAppFromAsar=off` | 让 Electron 读 `app/` 文件夹而非 `app.asar` | Electron fuse |
| `EnableEmbeddedAsarIntegrityValidation=off` | 跳过 asar 完整性 SHA 校验 | Electron fuse |
| `GrantFileProtocolExtraPrivileges=off` | 禁用 file 协议限制 | Electron fuse |
| `EnableCookieEncryption=off` | 禁用 cookie 加密检查 | Electron fuse |
| `mv app.asar app.asar1` | Electron 在 asar 不存在时自动降级到 `app/` 文件夹 | Resources 目录 |
| `codesign --force --deep --sign -` | macOS 拒绝启动未签名的修改应用 | 最终步骤 (仅 macOS) |
| Statsig 实验绕过 | API key 模式无 Statsig 用户上下文，i18n/特性实验默认关闭 | webview JS |
| `authMethod` 门控绕过 | 多处功能检查 `=== 'chatgpt'`，API key 模式被排除 | webview JS |
