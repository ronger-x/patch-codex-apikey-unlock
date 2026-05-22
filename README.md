# Codex App — API Key 模式全功能解锁

解锁 Codex 桌面应用在 API key 模式下的全部功能，使其与 ChatGPT 账号模式体验完全一致。

## 解锁功能

| 功能 | 说明 |
|------|------|
| Fast/Speed 模式 | 移除 `authMethod !== 'chatgpt'` 限制 |
| Plugins 插件侧边栏 | 门控变量强制为 `0` |
| 插件连接器可用性 | 禁用 `connector-unavailable` 标记 |
| 品牌视觉统一 | 强制返回 `false`（不区分账号类型） |
| 语音输入/听写 | 扩展为 `chatgpt \|\| apikey` |
| 用量/计费设置 | 扩展为 `chatgpt \|\| apikey` |
| i18n 多语言 | 绕过 Statsig 实验门控，强制启用 |

## 前置要求

- Node.js（用于 `npx @electron/asar` 和 `@electron/fuses`）
- Python 3

## 文件说明

```
patch.py                  主脚本：跨平台一键完整流程（推荐，macOS / Windows 通用）
patch-codex-windows.ps1   备用：纯 PowerShell 版，仅适用 Windows
SKILL.md                  完整技术文档（含版本更新排查指南）
```

## 使用方法

### 完整流程（推荐）

路径全部自动检测，无需任何参数：

```bash
# macOS / Linux
python3 patch.py

# Windows PowerShell
python3 patch.py
```

> 自动完成：kill 进程 → 检测安装 → 复制（Store 版）→ 提取 asar → 打 JS 补丁 → 禁用 fuses → 创建快捷方式

### 仅重新打 JS 补丁（Codex 更新后重新适配）

```bash
# macOS
python3 patch.py --assets "/Applications/Codex.app/Contents/Resources/app/webview/assets"

# Windows（Store 版 Codex-Patched）
python3 patch.py --assets "$env:LOCALAPPDATA\Programs\Codex-Patched\resources\app\webview\assets"
```

### 预演（不写入任何文件）

```bash
python3 patch.py --dry-run
```

## 回滚

**Windows Store 版：**
```powershell
Remove-Item -Recurse -Force "$env:LOCALAPPDATA\Programs\Codex-Patched"
# 原 Store 版未动，直接从开始菜单启动即可
```

**Windows 传统安装版：**
```powershell
cd "$env:LOCALAPPDATA\Programs\Codex\resources"
Remove-Item -Recurse -Force app
if (Test-Path app.asar1) { Rename-Item app.asar1 app.asar }
```

## config.toml 参考配置

```toml
# ~/.codex/config.toml
model_provider = "openai"
base_url = "https://your-api-provider.com/v1"
experimental_bearer_token = "sk-your-api-key"

[features]
enable_fast = true
```

## 版本更新说明

Codex 更新后 JS 文件名（hash 后缀）和变量名都可能变化。脚本内置了自动降级搜索，通常无需修改即可适配新版本。若有补丁失败，参考 [SKILL.md](SKILL.md) 的**版本更新排查指南**。
