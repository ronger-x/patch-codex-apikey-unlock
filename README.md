# ChatGPT Codex — API Key 模式全功能解锁

适配显示名升级为 **ChatGPT** 的 Codex 桌面应用，解锁 API key 模式下的模型列表、推理强度和服务层级等功能。

已针对 Windows Store `26.707.3748.0` 和 macOS app `26.707.31428`（Apple Silicon）验证。该版本的显示名和主入口已变为 ChatGPT，但安装包仍可能使用 Codex 名称；脚本同时兼容新旧名称。

## 解锁功能

| 功能 | 说明 |
|------|------|
| 最新模型列表 | 展示 app-server 已返回、但默认标记为 hidden 的模型 |
| 隐藏思考强度选项 | API key 模式展示模型实际支持、但被 ChatGPT 实验配置隐藏的全部思考强度 |
| Fast/Speed 模式 | 同时解除 UI 和实际请求链路的 `chatgpt` 登录限制 |
| Plugins 插件市场 | 兼容旧门控；新版原生支持 API key 时自动跳过 |
| Browser / Chrome | 解除 API key 缺少 Statsig 上下文导致的桌面可用性门控，并保留真实平台与 app-server 能力检查 |
| Computer Use | 启用桌面可用性与 Node runtime；macOS 使用 OpenAI 原签名的独立辅助服务 |
| 旧版连接器 UI 门控 | 仅兼容旧版前端门控；不伪造 ChatGPT 会话型连接器身份 |
| 品牌视觉统一 | 强制返回 `false`（不区分账号类型） |
| 语音输入/听写 | 扩展为 `chatgpt \|\| apikey` |
| 用量/计费设置 | 扩展为 `chatgpt \|\| apikey` |
| i18n 多语言 | 绕过 Statsig 实验门控，强制启用 |

## 前置要求

- Node.js（脚本固定使用兼容旧版 Node 的 `@electron/asar` / `@electron/fuses` 版本）
- Python 3

## 文件说明

```
patch.py                  主脚本：ChatGPT/Codex 双名称一键流程（macOS / Windows）
SKILL.md                  完整技术文档（含版本更新排查指南）
tests/test_patch.py       26.707 模型/思考强度/Fast/Browser/CUA 与 macOS 流程回归测试
```

## 使用方法

### 完整流程（推荐）

路径全部自动检测，无需任何参数：

```bash
# macOS
python3 patch.py

# Windows PowerShell
python3 patch.py
```

> 自动完成：关闭进程 → 检测安装 → 创建独立副本 → 提取/重打包 asar → 打 JS 补丁 → 平台完整性处理与签名

### macOS 路径

默认按顺序检测 `/Applications` 和 `~/Applications` 中的 `ChatGPT.app` / `Codex.app`。补丁副本优先放在官方 app 的同级目录；同级目录不可写时自动使用 `~/Applications`。

也可显式指定路径：

```bash
python3 patch.py \
  --app "$HOME/Applications/Codex.app" \
  --output "$HOME/Applications/ChatGPT-Codex-Patched.app"
```

脚本不会修改官方 app。macOS 副本会更新 ASAR 完整性信息、移除 quarantine 标记并进行 ad-hoc 签名；官方签名的运行时框架和 helper 保持不变。为让 ad-hoc 外层加载这些内部组件，外层签名会禁用 library validation，这会降低其动态库注入防护，因此该处理只用于独立副本。

### macOS Browser 与 Computer Use

`26.707.31428` 已验证内置 Browser、Chrome 插件和 Computer Use 会进入运行时市场。Computer Use 的辅助 app 保持 OpenAI 签名；系统权限项显示为 **Codex Computer Use**（bundle id `com.openai.sky.CUAService`），不是 ChatGPT。首次使用时请在以下两处完成授权：

- `系统设置 > 隐私与安全性 > 辅助功能`
- `系统设置 > 隐私与安全性 > 屏幕与系统音频录制`（旧版 macOS 名为“屏幕录制”）

补丁副本的最外层 app 是 ad-hoc 签名，macOS 无法为 Browser native pipe 解析完整 responsible-process 签名链。脚本只把 `missing-code-signing-identity` 降级为允许；`untrusted-code-signing-identity` 和缺失 socket descriptor 仍会拒绝，并把 Browser socket 收紧为 owner-only `0600`。该回退仍会削弱同一用户下的进程签名隔离。官方 app 保持不变，可作为 Appshots 和原始主 app TCC 身份的回退。

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

**macOS：**
```bash
rm -rf "/Applications/ChatGPT-Codex-Patched.app"
# 若副本位于用户目录：
rm -rf "$HOME/Applications/ChatGPT-Codex-Patched.app"
# 旧品牌副本名为 Codex-Patched.app
```

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

保留现有 API provider 配置即可。补丁不写死模型 ID 或推理强度，也不依赖旧版的 `features.enable_fast`：模型和推理强度来自 app-server 的实时列表，包括 Max/Ultra 在内的选项都只会在模型实际声明支持时出现；Fast 选择会写入当前版本使用的 `service_tier` 配置。

## 版本更新说明

ChatGPT Codex 更新后 JS 文件名（hash 后缀）和变量名都可能变化。脚本内置了自动降级搜索，通常无需修改即可适配新版本。若有补丁失败，参考 [SKILL.md](SKILL.md) 的**版本更新排查指南**。
