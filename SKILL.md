---
name: patch-codex-apikey-unlock
description: |
  Patch ChatGPT Codex (macOS/Windows) - API Key 模式功能解锁。
  兼容 ChatGPT/Codex 双品牌名称，显示 app-server 返回的最新隐藏模型，
  并解除 Fast/Speed 模式的 UI 与请求级认证门控。
version: 4.0.0
---

# Patch ChatGPT Codex - API Key 模式功能解锁

## 方案概述

使用 API key 模式时，解除桌面端仍依赖 ChatGPT 登录上下文的前端门控。

已验证 Windows Store `26.707.3748.0`（app `26.707.31428`）：显示名与主入口为 `ChatGPT` / `ChatGPT.exe`，MSIX 包身份仍为 `OpenAI.Codex`。脚本对这三者分别检测，并保留旧 Codex 名称回退。

### 解锁功能清单

| # | 功能 | 原始限制 | 补丁方式 |
|---|------|----------|----------|
| 1 | 最新模型列表 | API key 无 ChatGPT hidden-model 白名单 | 展示 app-server 已返回的全部模型 |
| 2 | Fast/Speed UI | `authMethod !== 'chatgpt'` 时隐藏 | 放开服务层级选择器 |
| 3 | Fast 请求层级 | 发请求前再次限定 `chatgpt` | 将 `apikey` 加入允许列表 |
| 4 | Plugins | 旧版限制非 ChatGPT；新版已原生支持 API key | 旧版补丁 / 新版自动跳过 |
| 5 | 语音输入/听写 | 仅 chatgpt 模式 | 扩展为 `chatgpt \|\| apikey` |
| 6 | 用量/计费设置 | 仅 chatgpt 模式 | 扩展为 `chatgpt \|\| apikey` |
| 7 | i18n 多语言 | API key 无 Statsig 用户上下文 | 强制启用 |

## 前置要求

- Python 3
- Node.js（用于 `npx @electron/asar` 和 `@electron/fuses`）

## 一键使用（推荐）

```bash
# macOS / Windows，路径自动检测
python3 patch.py

# 仅检查/调试 JS 补丁（不重新打包 app.asar）
python3 patch.py --assets /path/to/webview/assets

# 预演，不写入任何文件
python3 patch.py --dry-run
```

`patch.py` 自动完成所有步骤：关闭进程 → 检测安装类型 → 复制（Store 版）→ 提取 asar → 打 JS 补丁 → 禁用 Electron fuses → 创建快捷方式（Windows Store）/ 重签名（macOS）。

## macOS

脚本按顺序检测 `/Applications/ChatGPT.app` 和 `/Applications/Codex.app`，读取 `Info.plist` 的真实可执行文件，并要求 Resources 中存在 Codex 运行时标记。官方 app 不会被修改；补丁写入 `/Applications/ChatGPT-Codex-Patched.app`（旧品牌为 `Codex-Patched.app`）。

### 一键补丁

```bash
python3 patch.py
```

### 回滚

```bash
rm -rf /Applications/ChatGPT-Codex-Patched.app
# 旧品牌版本：rm -rf /Applications/Codex-Patched.app
```

官方 app 保持原签名，可直接继续使用。


---

## Windows

### 回滚方法

**Store 版（MSIX，ChatGPT 品牌）：**
```powershell
# 直接删除补丁目录，原 Store 版未动
Remove-Item -Recurse -Force "$env:LOCALAPPDATA\Programs\ChatGPT-Codex-Patched"
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

> 自动检测 Microsoft Store 版（MSIX）和传统安装版。MSIX 入口从 `AppxManifest.xml` 读取，并验证 Codex 资源标记。
> ChatGPT 品牌的 Store 版复制到 `%LOCALAPPDATA%\Programs\ChatGPT-Codex-Patched`（原版不动），桌面快捷方式为 `ChatGPT Codex (Patched)`。

---

## config.toml

保留现有 API provider 配置。补丁不写死模型 ID，也不依赖旧版 `features.enable_fast`：模型来自 app-server 的 `model/list`（请求已带 `includeHidden: true`），Fast 选择使用当前版本的 `service_tier`。

---

## 版本更新排查指南

ChatGPT Codex 更新后 JS 文件名（hash 后缀）和变量名都可能变化。以下是每个补丁的定位方法：

### 通用搜索策略

```bash
# 用 --assets 参数只检查/打 JS 补丁
python3 patch.py --assets /Applications/ChatGPT-Codex-Patched.app/Contents/Resources/app/webview/assets
# Windows (Store 版)
python3 patch.py --assets "$env:LOCALAPPDATA\Programs\ChatGPT-Codex-Patched\resources\app\webview\assets"

# 手动搜索（macOS）
cd /Applications/ChatGPT-Codex-Patched.app/Contents/Resources/app/webview/assets
```

### 1. 最新模型列表

```bash
grep -rl "useHiddenModels" *.js
# 26.707 的目标文件为 model-list-filter-*.js。
# 原条件：if(useAllowlist?allowed.has(model.model):!model.hidden)
# 新条件仅对 API key 放开：
# if(authMethod===`apikey`||(useAllowlist?allowed.has(model.model):!model.hidden))
```

### 2. Fast UI / 服务层级门控

```bash
grep -rl "isServiceTierAllowed" *.js
# use-service-tier-settings-*.js 中：
# allowed=auth?.authMethod===`chatgpt`,method=auth?.authMethod??null
# 将 allowed 改为：
# auth?.authMethod===`apikey`||auth?.authMethod===`chatgpt`
```

### 3. Fast 实际请求门控

```bash
grep -rl "Failed to read service tier for request" *.js
# read-service-tier-for-request-*.js 中：
# if(method!==`chatgpt`)return!1
# 改为：if(method!==`chatgpt`&&method!==`apikey`)return!1
```

### 4. Plugins

```bash
grep -rl "useHiddenOpenAICuratedMarketplaces" *.js
# 26.707 的 use-plugins-*.js 已包含：
# return method!==`chatgpt`&&method!==`apikey`&&...
# 这表示 API key 已原生允许，脚本应报告 SKIP，而不是误改
# plugin detail 页中仅用于展示原因的 connector-unavailable 文本。
```

旧版若仍有 `check-plugin-availability-*.js` 中的赋值门控，脚本继续使用 `false&&` 兼容补丁。该 UI 补丁不会伪造 `/aip/connectors` 所需的 ChatGPT 会话身份，因此不要把新版 ChatGPT 会话型连接器描述为 API key 原生可用。

### 5. 语音输入

```bash
grep -rn "authMethod===.chatgpt." *.js | grep -v "!=="
# 找到 xxx&&yyy.authMethod===`chatgpt` 且在 annotation/comment/editor 相关文件中
# 扩展为 xxx&&(yyy.authMethod===`chatgpt`||yyy.authMethod===`apikey`)
```

### 6. 用量设置

```bash
grep -rn "let.*===.chatgpt." *.js
# 找到 let r=e===`chatgpt` 且在 usage-settings 相关文件中
# 扩展为 let r=e===`chatgpt`||e===`apikey`
```

### 7. i18n 多语言

```bash
grep -rn "enable_i18n" *.js
# 找到 xxx=(0,YYY.useMemo)(()=>nnn?.get(`enable_i18n`,!1),[nnn])
# 改为 xxx=(0,YYY.useMemo)(()=>!0,[nnn])
# 关键: Statsig 实验门控在无用户上下文时默认返回 false
```

## 原理说明

| 操作 | 原因 | 位置 |
|------|------|------|
| 从 manifest/plist 读取入口 | ChatGPT 显示名、包身份和可执行文件名不再一致 | 安装发现 |
| 提取后重新打包 `app.asar` | Owl 运行时不会回退加载松散的 `app/` 目录 | Resources 目录 |
| 保留 `.node` / 原生模块 sidecar | Electron 必须从磁盘加载原生扩展 | `app.asar.unpacked` |
| `codesign --force --deep --sign -` | macOS 拒绝启动签名失效的修改副本 | 最终步骤 (仅 macOS) |
| hidden-model 过滤绕过 | API key 没有 ChatGPT Statsig 模型白名单 | `model-list-filter-*` |
| 请求级服务层级放行 | 仅显示 Fast 选项不足以改变实际请求 | `read-service-tier-for-request-*` |
| Statsig 实验绕过 | API key 模式无 Statsig 用户上下文，i18n/特性实验默认关闭 | webview JS |
| `authMethod` 门控绕过 | 多处功能检查 `=== 'chatgpt'`，API key 模式被排除 | webview JS |
