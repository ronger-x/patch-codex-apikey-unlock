---
name: patch-codex-apikey-unlock
description: |
  Patch ChatGPT Codex (macOS/Windows) - API Key 模式功能解锁。
  兼容 ChatGPT/Codex 双品牌名称，显示 app-server 返回的最新隐藏模型，
  展示模型声明但被实验配置隐藏的思考强度，并解除 Fast/Speed、Browser 与
  Computer Use 的 API key 门控。
version: 4.4.0
---

# Patch ChatGPT Codex - API Key 模式功能解锁

## 方案概述

使用 API key 模式时，解除桌面端仍依赖 ChatGPT 登录上下文的前端门控。

已验证 Windows Store `26.707.3748.0` 和 macOS app `26.707.31428`（Apple Silicon）。显示名与主入口已变为 ChatGPT，但包名/目录仍可能使用 Codex；脚本分别读取 MSIX manifest 或 macOS `Info.plist`，并保留旧名称回退。

### 解锁功能清单

| # | 功能 | 原始限制 | 补丁方式 |
|---|------|----------|----------|
| 1 | 最新模型列表 | API key 无 ChatGPT hidden-model 白名单 | 展示 app-server 已返回的全部模型 |
| 2 | 隐藏思考强度选项 | API key 无 ChatGPT 实验上下文 | 展示模型在 `supportedReasoningEfforts` 中实际声明的全部选项 |
| 3 | Fast/Speed UI | `authMethod !== 'chatgpt'` 时隐藏 | 放开服务层级选择器 |
| 4 | Fast 请求层级 | 发请求前再次限定 `chatgpt` | 将 `apikey` 加入允许列表 |
| 5 | Plugins | 旧版限制非 ChatGPT；新版已原生支持 API key | 旧版补丁 / 新版自动跳过 |
| 6 | Browser / Chrome | API key 无 Statsig 用户上下文 | 开启桌面 gate，保留平台与 app-server 能力检查 |
| 7 | Computer Use | 可用性与 Node runtime 受 Statsig gate 限制 | 开启两个 gate，保留原生服务认证与 TCC 检查 |
| 8 | 语音输入/听写 | 仅 chatgpt 模式 | 扩展为 `chatgpt \|\| apikey` |
| 9 | 用量/计费设置 | 仅 chatgpt 模式 | 扩展为 `chatgpt \|\| apikey` |
| 10 | i18n 多语言 | API key 无 Statsig 用户上下文 | 强制启用 |

## 前置要求

- Python 3
- Node.js（脚本固定使用 `@electron/asar@3.4.1` / `@electron/fuses@1.8.0`，避免最新版强制要求 Node 22.12+）

## 一键使用（推荐）

```bash
# macOS / Windows，路径自动检测
python3 patch.py

# 仅检查/调试 JS 补丁（不重新打包 app.asar）
python3 patch.py --assets /path/to/webview/assets

# 预演，不写入任何文件
python3 patch.py --dry-run
```

`patch.py` 自动完成所有步骤：关闭进程 → 检测安装类型 → 创建独立副本 → 提取/重打包 asar → 打 JS 补丁 → Windows fuse/快捷方式处理或 macOS ASAR 完整性更新与签名。

## macOS

脚本按顺序检测 `/Applications` 和 `~/Applications` 中的 `ChatGPT.app` / `Codex.app`，读取 `Info.plist` 的真实可执行文件，并要求 Resources 中存在 Codex 运行时标记。官方 app 不会被修改；补丁写入同级目录，同级不可写时回退到 `~/Applications`。新品牌副本名为 `ChatGPT-Codex-Patched.app`，旧品牌为 `Codex-Patched.app`。

### 一键补丁

```bash
python3 patch.py

# 自定义安装与输出路径
python3 patch.py --app /path/to/Codex.app \
  --output "$HOME/Applications/ChatGPT-Codex-Patched.app"
```

完成时脚本会刷新 `Info.plist` 中的 `ElectronAsarIntegrity` header hash，只对最外层 app 做 ad-hoc 签名，并严格验证整个 bundle。签名会移除仅对 OpenAI Team ID 有效的 application-group/keychain entitlements，保留 JIT、音频、相机、网络和自动化等运行权限；内部 OpenAI 签名的 framework/helper 不会被改写。为加载这些异签名内部组件，外层 entitlement 会禁用 library validation，这会降低外层进程的动态库注入防护，因此该处理只用于独立副本。

### Browser 与 Computer Use

Browser/Chrome/Computer Use 的 bundled plugins 由 renderer 计算出的 desktop availability 决定。API key 会话缺少 ChatGPT Statsig 用户上下文时，原版会记录 `reason=statsig-disabled` 并把插件从运行时市场移除。补丁按 `featureName` 与 availability 字段定位三个 gate，不把 Statsig 数字 ID 或模型名当作补丁语义；平台、WSL、app-server experimental feature 与插件配置检查仍由原逻辑执行。Computer Use 的 Node runtime gate 单独处理。

macOS Computer Use 使用 OpenAI 原签名的 `Codex Computer Use.app`，bundle id 为 `com.openai.sky.CUAService`。系统设置中的权限名称是 **Codex Computer Use**，需同时检查“辅助功能”与“屏幕与系统音频录制”（旧系统名为“屏幕录制”），而不是查找 ChatGPT。其 service、client、`codex`、`node_repl` 和 Node runtime 均保持 OpenAI 签名。

最外层补丁副本必须 ad-hoc 签名。Browser native addon 在 macOS responsible-process 归因到该外层进程时会返回 `missing-code-signing-identity`。补丁仅将这一结果降级为允许，继续拒绝 `untrusted-code-signing-identity` 与 `missing-socket-file-descriptor`，并把 Browser socket chmod 为 owner-only `0600`；不修改 `.node` 原生模块。安全代价是同一用户下缺少签名身份的进程仍会失去一部分隔离。官方 app 保持不变，作为 Appshots 与原始主 app TCC 身份的回退。

### 回滚

```bash
rm -rf /Applications/ChatGPT-Codex-Patched.app
rm -rf "$HOME/Applications/ChatGPT-Codex-Patched.app"
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

保留现有 API provider 配置。补丁不写死模型 ID、推理强度或旧版 `features.enable_fast`：模型和推理强度来自 app-server 的 `model/list`（请求已带 `includeHidden: true`），只展示模型在 `supportedReasoningEfforts` 中实际声明的选项，Fast 选择使用当前版本的 `service_tier`。

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

### 2. 隐藏思考强度选项

```bash
grep -rl "enabledReasoningEfforts" *.js
# 同一 model-list-filter-*.js 中有两层过滤：
# 1. includeUltraReasoningEffort 为 false 时移除 ultra
# 2. enabledReasoningEfforts.has(effort) 隐藏未启用档位
# API key 模式绕过这两层，但继续保留合法枚举校验；
# 不硬编码档位，只展示每个模型的 supportedReasoningEfforts。
```

API key 模式的完整列表显示在 Advanced/Effort 菜单；Max、Ultra 等选项仅在
当前模型实际声明支持时出现。简化 Power 滑杆使用官方固定组合，不在此补丁中修改。

### 3. Fast UI / 服务层级门控

```bash
grep -rl "isServiceTierAllowed" *.js
# use-service-tier-settings-*.js 中：
# allowed=auth?.authMethod===`chatgpt`,method=auth?.authMethod??null
# 将 allowed 改为：
# auth?.authMethod===`apikey`||auth?.authMethod===`chatgpt`
```

### 4. Fast 实际请求门控

```bash
grep -rl "Failed to read service tier for request" *.js
# read-service-tier-for-request-*.js 中：
# if(method!==`chatgpt`)return!1
# 改为：if(method!==`chatgpt`&&method!==`apikey`)return!1
```

### 5. Plugins

```bash
grep -rl "useHiddenOpenAICuratedMarketplaces" *.js
# 26.707 的 use-plugins-*.js 已包含：
# return method!==`chatgpt`&&method!==`apikey`&&...
# 这表示 API key 已原生允许，脚本应报告 SKIP，而不是误改
# plugin detail 页中仅用于展示原因的 connector-unavailable 文本。
```

旧版若仍有 `check-plugin-availability-*.js` 中的赋值门控，脚本继续使用 `false&&` 兼容补丁。该 UI 补丁不会伪造 `/aip/connectors` 所需的 ChatGPT 会话身份，因此不要把新版 ChatGPT 会话型连接器描述为 API key 原生可用。

### 6. Browser / Chrome

```bash
grep -rl 'featureName:`browser_use`' *.js
# 同一 desktop availability chunk 中应同时存在：
# featureName:`browser_use`          + isBrowserAgentGateEnabled
# featureName:`browser_use_external` + isExternalBrowserUseGateEnabled
# 脚本沿 availability 字段反查其 Statsig 赋值，并让原 gate 与
# authMethod === `apikey` 取 OR；两个 hook 始终都会执行。
# 不依赖压缩变量名，也不把当前 Statsig ID 写成定位条件。

# macOS Browser native pipe 回退位于 webview 同级的 .vite/build/main-*.js：
grep -rl 'browser-use-peer-authorization.node' ../../.vite/build/main-*.js
# 只允许 reason === `missing-code-signing-identity`；
# `untrusted-code-signing-identity` 与缺失 socket fd 必须继续拒绝。
```

### 7. Computer Use

```bash
grep -rl 'featureName:`computer_use`' *.js
# desktop availability 中定位 isComputerUseGateEnabled 对应的 Statsig 赋值。

grep -rl 'computerUseNodeRepl' *.js
# 同步给主进程的 computerUseNodeRepl 字段还有一个独立 runtime gate；
# 脚本让原 gate 与 API key 取 OR，不绕过 computer.available，
# 其他认证模式仍使用原 gate。
```

运行时验证时，marketplace 应包含 `computer-use`，并由保持 OpenAI 签名的 `SkyComputerUseService` 创建 group-container socket。TCC 项名称为 `Codex Computer Use`。不要通过修改原生二进制或 `codesign --deep` 绕过服务认证。

### 8. 语音输入

```bash
grep -rn "authMethod===.chatgpt." *.js | grep -v "!=="
# 找到 xxx&&yyy.authMethod===`chatgpt` 且在 annotation/comment/editor 相关文件中
# 扩展为 xxx&&(yyy.authMethod===`chatgpt`||yyy.authMethod===`apikey`)
```

### 9. 用量设置

```bash
grep -rn "let.*===.chatgpt." *.js
# 找到 let r=e===`chatgpt` 且在 usage-settings 相关文件中
# 扩展为 let r=e===`chatgpt`||e===`apikey`
```

### 10. i18n 多语言

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
| 更新 `ElectronAsarIntegrity` | 让重打包后的 ASAR header hash 与 `Info.plist` 一致 | `Info.plist` (仅 macOS) |
| 外层 ad-hoc 签名 + 严格验证 | 允许修改后的副本启动，同时保留内部 OpenAI 签名 | 最终步骤 (仅 macOS) |
| hidden-model 过滤绕过 | API key 没有 ChatGPT Statsig 模型白名单 | `model-list-filter-*` |
| reasoning-effort 过滤绕过 | API key 无 ChatGPT 实验上下文，enabled 集合可能隐藏模型声明的选项 | `model-list-filter-*` |
| 请求级服务层级放行 | 仅显示 Fast 选项不足以改变实际请求 | `read-service-tier-for-request-*` |
| desktop availability gate | API key 无 Statsig 用户上下文时 bundled plugins 会被移除 | desktop feature chunk |
| Computer Use runtime gate | 可用性成立后仍需选择 Node runtime | desktop feature sync chunk |
| Browser 缺失签名身份回退 | ad-hoc 外层 app 使 responsible-process 身份无法完整解析 | `.vite/build/main-*` (仅 macOS) |
| Statsig 实验绕过 | API key 模式无 Statsig 用户上下文，i18n/特性实验默认关闭 | webview JS |
| `authMethod` 门控绕过 | 多处功能检查 `=== 'chatgpt'`，API key 模式被排除 | webview JS |
