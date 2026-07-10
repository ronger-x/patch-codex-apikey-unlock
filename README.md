# ChatGPT Codex — API Key 模式全功能解锁

适配显示名升级为 **ChatGPT** 的 Codex 桌面应用，解锁 API key 模式下的模型列表、推理强度和服务层级等功能。

已针对 Windows Store `26.707.3748.0`（app `26.707.31428`）验证。该版本的显示名和主入口已变为 ChatGPT，但 Windows 包身份仍是 `OpenAI.Codex`；脚本同时兼容新旧名称。

## 解锁功能

| 功能 | 说明 |
|------|------|
| 最新模型列表 | 展示 app-server 已返回、但默认标记为 hidden 的模型 |
| 完整推理强度 | API key 模式在 Advanced/Effort 展示模型实际支持的 `none`、`minimal`、`low`、`medium`、`high`、`xhigh`、`max`、`ultra` |
| Fast/Speed 模式 | 同时解除 UI 和实际请求链路的 `chatgpt` 登录限制 |
| Plugins 插件市场 | 兼容旧门控；新版原生支持 API key 时自动跳过 |
| 旧版连接器 UI 门控 | 仅兼容旧版前端门控；不伪造 ChatGPT 会话型连接器身份 |
| 品牌视觉统一 | 强制返回 `false`（不区分账号类型） |
| 语音输入/听写 | 扩展为 `chatgpt \|\| apikey` |
| 用量/计费设置 | 扩展为 `chatgpt \|\| apikey` |
| i18n 多语言 | 绕过 Statsig 实验门控，强制启用 |

## 前置要求

- Node.js（用于 `npx @electron/asar` 和 `@electron/fuses`）
- Python 3

## 文件说明

```
patch.py                  主脚本：ChatGPT/Codex 双名称一键流程（macOS / Windows）
SKILL.md                  完整技术文档（含版本更新排查指南）
tests/test_patch.py       26.707 模型/推理/Fast 门控及幂等性回归测试
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

### 仅检查/调试 JS 补丁

```bash
# macOS（补丁副本）
python3 patch.py --assets "/Applications/ChatGPT-Codex-Patched.app/Contents/Resources/app/webview/assets"

# Windows（Store 版 ChatGPT-Codex-Patched）
python3 patch.py --assets "$env:LOCALAPPDATA\Programs\ChatGPT-Codex-Patched\resources\app\webview\assets"
```

`--assets` 不重新打包 `app.asar`，适合版本适配和测试。要让修改进入 Owl 运行时，请运行完整流程 `python3 patch.py`。

### 预演（不写入任何文件）

```bash
python3 patch.py --dry-run
```

## 回滚

**Windows Store 版：**
```powershell
Remove-Item -Recurse -Force "$env:LOCALAPPDATA\Programs\ChatGPT-Codex-Patched"
# 原 Store 版未动，直接从开始菜单启动即可
```

**Windows 传统安装版：**
```powershell
cd "$env:LOCALAPPDATA\Programs\ChatGPT\resources" # 旧品牌目录为 Codex
Remove-Item -Recurse -Force app
if (Test-Path app.asar.bak) { Copy-Item app.asar.bak app.asar -Force }
```

## config.toml

保留现有 API provider 配置即可。补丁不写死模型 ID，也不依赖旧版的 `features.enable_fast`：模型来自 app-server 的实时列表，Fast 选择会写入当前版本使用的 `service_tier` 配置。

## 版本更新说明

ChatGPT Codex 更新后 JS 文件名（hash 后缀）和变量名都可能变化。脚本内置了自动降级搜索，通常无需修改即可适配新版本。若有补丁失败，参考 [SKILL.md](SKILL.md) 的**版本更新排查指南**。
