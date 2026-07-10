import contextlib
import hashlib
import importlib.util
import io
import json
import plistlib
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
PATCH_SCRIPT = REPO_ROOT / "patch.py"


def write_supported_assets(assets):
    service_tier = assets / "use-service-tier-settings-test.js"
    request_tier = assets / "read-service-tier-for-request-test.js"
    model_filter = assets / "model-list-filter-test.js"
    native_plugins = assets / "use-plugins-test.js"
    desktop_features = assets / "desktop-feature-availability-test.js"
    desktop_feature_sync = assets / "desktop-feature-sync-test.js"
    auth_context = assets / "auth-context-test.js"

    (assets / "package.json").write_text('{"type":"module"}', encoding="utf-8")
    auth_context.write_text(
        "export const c={current:{authMethod:null}};", encoding="utf-8"
    )

    service_tier.write_text(
        "let auth=getAuth(),gate=auth?.authMethod===`chatgpt`,"
        "method=auth?.authMethod??null;"
        "return {isServiceTierAllowed:gate};",
        encoding="utf-8",
    )
    request_tier.write_text(
        "async function allowed(e,t){let method=await auth(e,t);"
        "if(method!==`chatgpt`)return!1;return true}",
        encoding="utf-8",
    )
    model_filter_source = (
        "const validEfforts=[`none`,`minimal`,`low`,`medium`,`high`,"
        "`xhigh`,`max`,`ultra`];"
        "let shown=[];"
        "function t(e){return validEfforts.includes(e)}"
        "function show(e){shown.push(e)}"
        "function tools({authMethod,enabledReasoningEfforts,"
        "includeUltraReasoningEffort}){"
        "return {authMethod,enabledReasoningEfforts,"
        "includeUltraReasoningEffort}}"
        "function filter({authMethod:e,availableModels:n,"
        "enabledReasoningEfforts:i,includeUltraReasoningEffort:a,"
        "models:o,useHiddenModels:s}){"
        "let u=s&&e!==`amazonBedrock`;o.forEach(r=>{"
        "if(u?n.has(r.model):!r.hidden){"
        "let x=a?r.supportedReasoningEfforts:"
        "r.supportedReasoningEfforts.filter("
        "({reasoningEffort:e})=>e!==`ultra`),"
        "y=(e===`copilot`?"
        "[x.find(e=>e.reasoningEffort===`medium`)??"
        "{reasoningEffort:`medium`}]:x).filter("
        "({reasoningEffort:e})=>t(e)&&i.has(e)),"
        "z={...r,supportedReasoningEfforts:y};show(z)"
        "}})}"
        "const efforts=[...validEfforts,`invalid`].map("
        "reasoningEffort=>({reasoningEffort}));"
        "function run(authMethod,enabled,includeUltra){shown=[];"
        "filter({authMethod,availableModels:new Set(),"
        "enabledReasoningEfforts:new Set(enabled),"
        "includeUltraReasoningEffort:includeUltra,"
        "models:[{model:`test`,hidden:false,isDefault:true,"
        "supportedReasoningEfforts:efforts}],useHiddenModels:false});"
        "return shown[0].supportedReasoningEfforts.map("
        "({reasoningEffort})=>reasoningEffort)}"
        "if(process.argv[2]===`--verify`){console.log(JSON.stringify({"
        "apikey:run(`apikey`,[`medium`],false),"
        "chatgpt:run(`chatgpt`,[`medium`,`max`,`ultra`,`invalid`],false),"
        "copilot:run(`copilot`,[...validEfforts,`invalid`],true)}))}"
    )
    model_filter.write_text(model_filter_source, encoding="utf-8")
    native_plugin_source = (
        "function supported(method){return "
        "method!==`chatgpt`&&method!==`apikey`&&method!==`amazonBedrock`}"
    )
    native_plugins.write_text(native_plugin_source, encoding="utf-8")
    desktop_features.write_text(
        "import{c as x}from\"./auth-context-test.js\";"
        "const React={useContext:context=>context.current};"
        "let statsigEnabled=false;"
        "function statsig(){return statsigEnabled}"
        "function resolve(value){return value}"
        "function auth(){return(0,React.useContext)(x)?.authMethod"
        "===`chatgpt`}"
        "function computer(host){let gate=statsig(`1506311413`),config;"
        "config={featureName:`computer_use`,hostId:host};"
        "let x=`loading`;"
        "return resolve({feature:config,isComputerUseGateEnabled:gate,shadow:x})}"
        "function external(host){let gate=statsig(`410065390`),config;"
        "config={featureName:`browser_use_external`,hostId:host};"
        "return resolve({feature:config,isExternalBrowserUseGateEnabled:gate})}"
        "function browser(host){let gate=statsig(`410262010`),config;"
        "config={featureName:`browser_use`,hostId:host};"
        "return resolve({feature:config,isBrowserAgentGateEnabled:gate})}"
        "function run(method,enabled){x.current={authMethod:method};"
        "statsigEnabled=enabled;return["
        "computer(`host`).isComputerUseGateEnabled,"
        "external(`host`).isExternalBrowserUseGateEnabled,"
        "browser(`host`).isBrowserAgentGateEnabled]}"
        "if(process.argv[2]===`--verify`){console.log(JSON.stringify({"
        "apikeyFalse:run(`apikey`,false),"
        "chatgptFalse:run(`chatgpt`,false),"
        "copilotFalse:run(`copilot`,false),"
        "bedrockFalse:run(`amazonBedrock`,false),"
        "chatgptTrue:run(`chatgpt`,true)}))}",
        encoding="utf-8",
    )
    desktop_feature_sync.write_text(
        "function auth(){let{authMethod}=useAuth();return authMethod}"
        "function sync(){let computer=getComputer(),nodeGate=statsig(`2212532336`);"
        "send({computerUseNodeRepl:computer.available&&nodeGate})}",
        encoding="utf-8",
    )

    return {
        "service_tier": service_tier,
        "request_tier": request_tier,
        "model_filter": model_filter,
        "model_filter_source": model_filter_source,
        "native_plugins": native_plugins,
        "native_plugin_source": native_plugin_source,
        "desktop_features": desktop_features,
        "desktop_feature_sync": desktop_feature_sync,
        "auth_context": auth_context,
    }


@contextlib.contextmanager
def loaded_patch_module():
    """Load patch.py through its supported assets-only path for unit testing."""
    with tempfile.TemporaryDirectory() as tmp:
        assets = Path(tmp)
        write_supported_assets(assets)
        spec = importlib.util.spec_from_file_location("patch_under_test", PATCH_SCRIPT)
        module = importlib.util.module_from_spec(spec)
        with mock.patch.object(
            sys, "argv", [str(PATCH_SCRIPT), "--assets", str(assets)]
        ), contextlib.redirect_stdout(io.StringIO()):
            spec.loader.exec_module(module)
        yield module


class ChatGPTCodexPatchTests(unittest.TestCase):
    def test_26707_model_reasoning_and_fast_gates_are_patched_idempotently(self):
        with tempfile.TemporaryDirectory() as tmp:
            assets = Path(tmp)
            fixture = write_supported_assets(assets)
            service_tier = fixture["service_tier"]
            request_tier = fixture["request_tier"]
            model_filter = fixture["model_filter"]
            native_plugins = fixture["native_plugins"]
            native_plugin_source = fixture["native_plugin_source"]
            desktop_features = fixture["desktop_features"]
            desktop_feature_sync = fixture["desktop_feature_sync"]
            model_filter_source = fixture["model_filter_source"]

            first = self.run_patch(assets)
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            self.assertNotIn("[FAIL]", first.stdout)
            self.assertIn(
                "[SKIP] 品牌视觉/插件市场统一 (新版已原生支持 API key)",
                first.stdout,
            )
            self.assertEqual(native_plugin_source, native_plugins.read_text("utf-8"))
            self.assertIn(
                "gate=auth?.authMethod===`apikey`||"
                "auth?.authMethod===`chatgpt`",
                service_tier.read_text("utf-8"),
            )
            self.assertIn(
                "method!==`chatgpt`&&method!==`apikey`", request_tier.read_text("utf-8")
            )
            self.assertIn(
                "if(e===`apikey`||(u?n.has(r.model):!r.hidden))",
                model_filter.read_text("utf-8"),
            )
            self.assertIn(
                "x=(e===`apikey`||a)?r.supportedReasoningEfforts",
                model_filter.read_text("utf-8"),
            )
            self.assertIn(
                "t(__codexReasoningEffort)&&"
                "(e===`apikey`||i.has(__codexReasoningEffort))",
                model_filter.read_text("utf-8"),
            )
            self.assertIn(
                "u=s&&e!==`amazonBedrock`", model_filter.read_text("utf-8")
            )
            self.assertIn(
                "y=(e===`copilot`?[x.find(e=>e.reasoningEffort===`medium`)",
                model_filter.read_text("utf-8"),
            )

            node = shutil.which("node")
            if node is None:
                self.fail("Node.js is required for reasoning-effort semantic tests")
            semantic_result = subprocess.run(
                [node, str(model_filter), "--verify"],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            self.assertEqual(
                semantic_result.returncode,
                0,
                semantic_result.stdout + semantic_result.stderr,
            )
            semantic_output = json.loads(semantic_result.stdout)
            self.assertEqual(
                semantic_output["apikey"],
                ["none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"],
            )
            self.assertEqual(semantic_output["chatgpt"], ["medium", "max"])
            self.assertEqual(semantic_output["copilot"], ["medium"])

            feature_content = desktop_features.read_text("utf-8")
            self.assertIn(
                "function useCodexApiKeyAuth(){return(0,React.useContext)(x)"
                "?.authMethod===`apikey`}",
                feature_content,
            )
            self.assertIn(
                "gate=[statsig(`1506311413`),useCodexApiKeyAuth()]"
                ".some(Boolean)",
                feature_content,
            )
            self.assertIn(
                "gate=[statsig(`410065390`),useCodexApiKeyAuth()]"
                ".some(Boolean)",
                feature_content,
            )
            self.assertIn(
                "gate=[statsig(`410262010`),useCodexApiKeyAuth()]"
                ".some(Boolean)",
                feature_content,
            )
            self.assertEqual(
                feature_content.count(
                    "(0,React.useContext)(x)?.authMethod===`apikey`"
                ),
                1,
            )
            self.assertIn(
                "nodeGate=[statsig(`2212532336`),useAuth()?.authMethod==="
                "`apikey`].some(Boolean)",
                desktop_feature_sync.read_text("utf-8"),
            )
            self.assertNotIn("true||statsig", feature_content)
            self.assertIn("featureName:`computer_use`", feature_content)
            self.assertIn("featureName:`browser_use`", feature_content)

            desktop_result = subprocess.run(
                [node, str(desktop_features), "--verify"],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            self.assertEqual(
                desktop_result.returncode,
                0,
                desktop_result.stdout + desktop_result.stderr,
            )
            desktop_output = json.loads(desktop_result.stdout)
            self.assertEqual(desktop_output["apikeyFalse"], [True, True, True])
            self.assertEqual(desktop_output["chatgptFalse"], [False, False, False])
            self.assertEqual(desktop_output["copilotFalse"], [False, False, False])
            self.assertEqual(desktop_output["bedrockFalse"], [False, False, False])
            self.assertEqual(desktop_output["chatgptTrue"], [True, True, True])

            contents_after_first_run = {
                path.name: path.read_text("utf-8") for path in assets.glob("*.js")
            }
            second = self.run_patch(assets)
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            self.assertNotIn("[FAIL]", second.stdout)
            self.assertIn("[SKIP] 服务层级授权门控", second.stdout)
            self.assertIn("[SKIP] Fast 请求服务层级门控", second.stdout)
            self.assertIn("[SKIP] 隐藏模型列表解锁", second.stdout)
            self.assertIn("[SKIP] 推理强度列表解锁", second.stdout)
            self.assertIn("[SKIP] 内置 Browser 可用性", second.stdout)
            self.assertIn("[SKIP] 外部 Browser 可用性", second.stdout)
            self.assertIn("[SKIP] Computer Use 可用性", second.stdout)
            self.assertIn("[SKIP] Computer Use Node runtime", second.stdout)
            self.assertEqual(
                contents_after_first_run,
                {path.name: path.read_text("utf-8") for path in assets.glob("*.js")},
            )

            upgraded_model_filter = model_filter_source.replace(
                "if(u?n.has(r.model):!r.hidden){",
                "if(e===`apikey`||(u?n.has(r.model):!r.hidden)){",
            )
            model_filter.write_text(upgraded_model_filter, encoding="utf-8")
            upgrade_result = self.run_patch(assets)
            self.assertEqual(
                upgrade_result.returncode,
                0,
                upgrade_result.stdout + upgrade_result.stderr,
            )
            self.assertIn("[SKIP] 隐藏模型列表解锁", upgrade_result.stdout)
            self.assertIn("[OK]   推理强度列表解锁", upgrade_result.stdout)
            fully_patched_model_filter = model_filter.read_text("utf-8")

            ultra_only_model_filter = model_filter_source.replace(
                "let x=a?r.supportedReasoningEfforts:",
                "let x=(e===`apikey`||a)?r.supportedReasoningEfforts:",
            )
            self.assertNotEqual(ultra_only_model_filter, model_filter_source)
            model_filter.write_text(ultra_only_model_filter, encoding="utf-8")
            ultra_only_result = self.run_patch(assets)
            self.assertEqual(
                ultra_only_result.returncode,
                0,
                ultra_only_result.stdout + ultra_only_result.stderr,
            )
            self.assertIn("[OK]   推理强度列表解锁", ultra_only_result.stdout)
            self.assertIn(
                "(e===`apikey`||i.has(__codexReasoningEffort))",
                model_filter.read_text("utf-8"),
            )

            enabled_only_model_filter = model_filter_source.replace(
                "({reasoningEffort:e})=>t(e)&&i.has(e))",
                "({reasoningEffort:effort})=>t(effort)&&"
                "(e===`apikey`||i.has(effort)))",
            )
            self.assertNotEqual(enabled_only_model_filter, model_filter_source)
            model_filter.write_text(enabled_only_model_filter, encoding="utf-8")
            enabled_only_result = self.run_patch(assets)
            self.assertEqual(
                enabled_only_result.returncode,
                0,
                enabled_only_result.stdout + enabled_only_result.stderr,
            )
            self.assertIn("[OK]   推理强度列表解锁", enabled_only_result.stdout)
            self.assertIn(
                "x=(e===`apikey`||a)?r.supportedReasoningEfforts",
                model_filter.read_text("utf-8"),
            )

            ultra_expression = (
                "a?r.supportedReasoningEfforts:"
                "r.supportedReasoningEfforts.filter("
                "({reasoningEffort:e})=>e!==`ultra`)"
            )
            duplicate_filter = model_filter_source.replace(
                f"let x={ultra_expression},",
                f"let q={ultra_expression},x={ultra_expression},",
            )
            self.assertNotEqual(duplicate_filter, model_filter_source)
            model_filter.write_text(duplicate_filter, encoding="utf-8")
            duplicate_result = self.run_patch(assets)
            self.assertNotEqual(duplicate_result.returncode, 0)
            self.assertIn("推理强度列表解锁: 目标结构不匹配", duplicate_result.stdout)
            self.assertEqual(duplicate_filter, model_filter.read_text("utf-8"))
            model_filter.write_text(fully_patched_model_filter, encoding="utf-8")

            return_variants = (
                "function supported(method){return(method!==`chatgpt`&&"
                "method!==`apikey`&&method!==`amazonBedrock`)}",
                "function supported(method){return   method!==`chatgpt`&&"
                "method!==`apikey`&&method!==`amazonBedrock`}",
                "function supported(method){return   (method!==`chatgpt`&&"
                "method!==`apikey`&&method!==`amazonBedrock`)}",
                "function supported ( method ) { return ( method !== `chatgpt` && "
                "method !== `apikey` && method !== `amazonBedrock` ) }",
            )
            for source in return_variants:
                with self.subTest(source=source):
                    native_plugins.write_text(source, encoding="utf-8")
                    result = self.run_patch(assets)
                    self.assertEqual(
                        result.returncode, 0, result.stdout + result.stderr
                    )
                    self.assertNotIn("[FAIL]", result.stdout)
                    self.assertEqual(source, native_plugins.read_text("utf-8"))
                    self.assertIn(
                        "[SKIP] 旧版插件连接器 UI 门控 "
                        "(新版已移除该旧补丁点)",
                        result.stdout,
                    )
                    self.assertIn(
                        "[SKIP] 品牌视觉/插件市场统一 "
                        "(新版已原生支持 API key)",
                        result.stdout,
                    )
                    self.assertNotIn("[OK] 品牌视觉/插件统一", result.stdout)

            drifted_model_filter = model_filter_source.replace(
                "includeUltraReasoningEffort:a,", ""
            )
            model_filter.write_text(drifted_model_filter, encoding="utf-8")
            drift_result = self.run_patch(assets)
            self.assertNotEqual(drift_result.returncode, 0)
            self.assertIn("推理强度列表解锁: 目标结构不匹配", drift_result.stdout)
            self.assertEqual(drifted_model_filter, model_filter.read_text("utf-8"))
            model_filter.write_text(fully_patched_model_filter, encoding="utf-8")

            asi_source = (
                "function supported(method){return\n"
                "method!==`chatgpt`&&method!==`apikey`}"
            )
            native_plugins.write_text(asi_source, encoding="utf-8")
            asi_result = self.run_patch(assets)
            self.assertNotEqual(asi_result.returncode, 0)
            self.assertNotIn("新版已原生支持 API key", asi_result.stdout)
            self.assertEqual(asi_source, native_plugins.read_text("utf-8"))

    def test_desktop_gate_patch_migrates_shadowing_pr5_form(self):
        with tempfile.TemporaryDirectory() as tmp:
            assets = Path(tmp)
            fixture = write_supported_assets(assets)
            desktop_features = fixture["desktop_features"]
            legacy = desktop_features.read_text("utf-8").replace(
                "let statsigEnabled=false;",
                "const useCodexApiKeyAuth=0;let statsigEnabled=false;",
            )
            for gate_id in ("1506311413", "410065390", "410262010"):
                legacy = legacy.replace(
                    f"gate=statsig(`{gate_id}`)",
                    f"gate=[statsig(`{gate_id}`),(0,React.useContext)(x)"
                    "?.authMethod===`apikey`].some(Boolean)",
                )
            desktop_features.write_text(legacy, encoding="utf-8")

            result = self.run_patch(assets)

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertNotIn("[FAIL]", result.stdout)
            patched = desktop_features.read_text("utf-8")
            self.assertIn(
                "function useCodexApiKeyAuth2(){return(0,React.useContext)(x)"
                "?.authMethod===`apikey`}",
                patched,
            )
            self.assertEqual(patched.count("useCodexApiKeyAuth2()].some(Boolean)"), 3)
            self.assertEqual(
                patched.count("(0,React.useContext)(x)?.authMethod===`apikey`"),
                1,
            )

            node = shutil.which("node")
            if node is None:
                self.fail("Node.js is required for desktop-gate semantic tests")
            semantic_result = subprocess.run(
                [node, str(desktop_features), "--verify"],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            self.assertEqual(
                semantic_result.returncode,
                0,
                semantic_result.stdout + semantic_result.stderr,
            )
            semantic_output = json.loads(semantic_result.stdout)
            self.assertEqual(semantic_output["apikeyFalse"], [True, True, True])
            self.assertEqual(semantic_output["chatgptFalse"], [False, False, False])

            migrated_content = desktop_features.read_text("utf-8")
            second = self.run_patch(assets)
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            self.assertIn("[SKIP] Computer Use 可用性", second.stdout)
            self.assertEqual(migrated_content, desktop_features.read_text("utf-8"))

    def test_windows_taskbar_identity_is_unique_idempotent_and_fails_closed(self):
        with loaded_patch_module() as patch_module, tempfile.TemporaryDirectory() as tmp:
            main_build = Path(tmp) / ".vite" / "build"
            main_build.mkdir(parents=True)
            identity_file = main_build / "file-based-logger-test.js"
            original = (
                "var flavors={Prod:`prod`};"
                "function appId(flavor){switch(flavor){"
                "case flavors.Dev:return`com.openai.codex.dev`;"
                "case flavors.Prod:return`com.openai.codex`}}"
            )
            identity_file.write_text(original, encoding="utf-8")
            patch_module.DRY_RUN = False
            patch_module.results = {"applied": [], "skipped": [], "failed": []}

            patch_module.apply_windows_app_user_model_id_patch(str(main_build))

            patched = identity_file.read_text("utf-8")
            self.assertIn(
                "case flavors.Prod:return`com.openai.codex.patched`", patched
            )
            self.assertEqual([], patch_module.results["failed"])
            self.assertEqual(1, len(patch_module.results["applied"]))

            patch_module.results = {"applied": [], "skipped": [], "failed": []}
            patch_module.apply_windows_app_user_model_id_patch(str(main_build))
            self.assertEqual(patched, identity_file.read_text("utf-8"))
            self.assertEqual(1, len(patch_module.results["skipped"]))

            drifted = original.replace(
                "return`com.openai.codex`", "return`com.openai.codex.store`"
            )
            identity_file.write_text(drifted, encoding="utf-8")
            patch_module.results = {"applied": [], "skipped": [], "failed": []}
            patch_module.apply_windows_app_user_model_id_patch(str(main_build))
            self.assertEqual(drifted, identity_file.read_text("utf-8"))
            self.assertEqual(1, len(patch_module.results["failed"]))

    def test_current_desktop_feature_marker_drift_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            assets = Path(tmp)
            fixture = write_supported_assets(assets)
            desktop_features = fixture["desktop_features"]
            drifted_content = desktop_features.read_text("utf-8").replace(
                "isBrowserAgentGateEnabled", "renamedBrowserGate"
            )
            desktop_features.write_text(drifted_content, encoding="utf-8")

            result = self.run_patch(assets)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("[FAIL] Browser / Computer Use 可用性", result.stdout)
            self.assertEqual(drifted_content, desktop_features.read_text("utf-8"))

    def test_macos_merged_chunks_are_discovered_and_patched_idempotently(self):
        with tempfile.TemporaryDirectory() as tmp:
            assets = Path(tmp)
            fixture = write_supported_assets(assets)
            fixture["model_filter"].unlink()
            fixture["native_plugins"].unlink()

            model_filter = assets / "app-initial-app-main-models.js"
            model_filter.write_text(
                "function unrelated({value}){return value}"
                "function valid(value){return value!==`invalid`}"
                "function show(value){return value}"
                "function filter({authMethod:e,availableModels:t,"
                "enabledReasoningEfforts:r,"
                "includeUltraReasoningEffort:i,models:a,useHiddenModels:o}){"
                "let l=o&&e!==`amazonBedrock`;"
                "a.forEach(item=>{if(l?t.has(item.model):!item.hidden){"
                "let x=i?item.supportedReasoningEfforts:"
                "item.supportedReasoningEfforts.filter("
                "({reasoningEffort:x})=>x!==`ultra`),"
                "y=(e===`copilot`?"
                "[x.find(x=>x.reasoningEffort===`medium`)??"
                "{reasoningEffort:`medium`}]:x).filter("
                "({reasoningEffort:x})=>valid(x)&&r.has(x)),"
                "z={...item,supportedReasoningEfforts:y};show(z)}})}",
                encoding="utf-8",
            )
            (assets / "app-main-decoy.js").write_text(
                "const unrelated=true;", encoding="utf-8"
            )
            i18n = assets / "app-initial-app-main-page.js"
            i18n.write_text(
                "function settings(a){let o=a?.get(`enable_i18n`,!1);return o}",
                encoding="utf-8",
            )
            native_plugins = assets / "app-initial-app-main-plugins.js"
            native_plugin_source = (
                "function supported(e){return e!==`chatgpt`&&e!==`apikey`&&"
                "e!==`amazonBedrock`}"
            )
            native_plugins.write_text(native_plugin_source, encoding="utf-8")
            dictation = assets / "app-initial-app-main-dictation.js"
            dictation.write_text(
                "function dictation(){return enabled&&auth.authMethod===`chatgpt`}",
                encoding="utf-8",
            )

            first = self.run_patch(assets)
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            self.assertNotIn("[FAIL]", first.stdout)
            self.assertIn(
                "if(e===`apikey`||(l?t.has(item.model):!item.hidden))",
                model_filter.read_text("utf-8"),
            )
            self.assertIn(
                "x=(e===`apikey`||i)?item.supportedReasoningEfforts",
                model_filter.read_text("utf-8"),
            )
            self.assertIn(
                "valid(__codexReasoningEffort)&&(e===`apikey`||"
                "r.has(__codexReasoningEffort))",
                model_filter.read_text("utf-8"),
            )
            self.assertNotIn(
                "new Set(a.flatMap", model_filter.read_text("utf-8")
            )
            self.assertIn(
                "o=true||a?.get(`enable_i18n`,!1)", i18n.read_text("utf-8")
            )
            self.assertEqual(native_plugin_source, native_plugins.read_text("utf-8"))
            self.assertIn(
                "enabled&&(auth.authMethod===`chatgpt`||"
                "auth.authMethod===`apikey`)",
                dictation.read_text("utf-8"),
            )
            self.assertIn(
                "[SKIP] 旧版插件连接器 UI 门控 (新版已移除该旧补丁点)",
                first.stdout,
            )
            self.assertIn(
                "[SKIP] 品牌视觉/插件市场统一 (新版已原生支持 API key)",
                first.stdout,
            )

            contents = {
                path.name: path.read_text("utf-8") for path in assets.glob("*.js")
            }
            second = self.run_patch(assets)
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            self.assertNotIn("[FAIL]", second.stdout)
            self.assertIn("[SKIP] 语音输入解锁", second.stdout)
            self.assertIn("[SKIP] 推理强度列表解锁", second.stdout)
            self.assertEqual(
                contents,
                {path.name: path.read_text("utf-8") for path in assets.glob("*.js")},
            )

    def test_macos_detects_chatgpt_bundle_and_targets_independent_copy(self):
        with (
            loaded_patch_module() as patch_module,
            tempfile.TemporaryDirectory() as tmp,
        ):
            app = Path(tmp) / "ChatGPT.app"
            executable = app / "Contents" / "MacOS" / "ChatGPT"
            resources = app / "Contents" / "Resources"
            executable.parent.mkdir(parents=True)
            resources.mkdir(parents=True)
            executable.touch()
            (resources / "app.asar").touch()
            (resources / "codex").touch()
            with (app / "Contents" / "Info.plist").open("wb") as fh:
                plistlib.dump({"CFBundleExecutable": "ChatGPT"}, fh)

            patch_module.IS_MACOS = True
            patch_module.IS_WINDOWS = False
            patch_module.MACOS_APP_PATHS = (str(app),)
            with contextlib.redirect_stdout(io.StringIO()):
                detected = patch_module.step_detect()

            self.assertEqual(
                detected,
                (str(app), str(resources), str(executable), False),
            )

            patch_module.DRY_RUN = True
            with contextlib.redirect_stdout(io.StringIO()):
                copied = patch_module.step_copy_macos(str(app))
            patched_app = str(Path(tmp) / "ChatGPT-Codex-Patched.app")
            self.assertEqual(
                copied,
                (
                    patched_app,
                    str(Path(patched_app) / "Contents" / "Resources"),
                    str(Path(patched_app) / "Contents" / "MacOS" / "ChatGPT"),
                ),
            )

    def test_macos_executable_falls_back_to_legacy_codex_name(self):
        with (
            loaded_patch_module() as patch_module,
            tempfile.TemporaryDirectory() as tmp,
        ):
            app = Path(tmp) / "Codex.app"
            executable = app / "Contents" / "MacOS" / "Codex"
            executable.parent.mkdir(parents=True)
            executable.touch()
            (app / "Contents" / "Info.plist").write_text(
                "not a plist", encoding="utf-8"
            )

            self.assertEqual(
                patch_module._macos_executable(str(app)), str(executable)
            )

    def test_macos_rejects_overlapping_copy_paths_even_in_dry_run(self):
        with (
            loaded_patch_module() as patch_module,
            tempfile.TemporaryDirectory() as tmp,
        ):
            app = Path(tmp) / "Codex.app"
            executable = app / "Contents" / "MacOS" / "Codex"
            executable.parent.mkdir(parents=True)
            executable.touch()
            patch_module.DRY_RUN = True

            with (
                contextlib.redirect_stdout(io.StringIO()),
                self.assertRaises(SystemExit),
            ):
                patch_module.step_copy_macos(
                    str(app), str(app / "Contents" / "Nested.app")
                )

            with (
                mock.patch.object(patch_module.os.path, "samefile", return_value=True),
                mock.patch.object(patch_module.os.path, "exists", return_value=True),
                contextlib.redirect_stdout(io.StringIO()),
                self.assertRaises(SystemExit),
            ):
                patch_module.step_copy_macos(str(app), str(Path(tmp) / "Alias.app"))

    def test_macos_asar_integrity_hash_uses_archive_header(self):
        with (
            loaded_patch_module() as patch_module,
            tempfile.TemporaryDirectory() as tmp,
        ):
            header = b'{"files":{"index.js":{"size":1,"offset":"0"}}}'
            string_pickle = struct.pack("<II", len(header) + 1, len(header))
            string_pickle += header + b"\0"
            string_pickle += b"\0" * (-len(string_pickle) % 4)
            size_pickle = struct.pack("<II", 4, len(string_pickle))
            app = Path(tmp) / "Patched.app"
            resources = app / "Contents" / "Resources"
            resources.mkdir(parents=True)
            asar = resources / "app.asar"
            asar.write_bytes(size_pickle + string_pickle + b"x")
            info_plist = app / "Contents" / "Info.plist"
            with info_plist.open("wb") as fh:
                plistlib.dump(
                    {
                        "ElectronAsarIntegrity": {
                            "Resources/app.asar": {
                                "algorithm": "SHA256",
                                "hash": "stale",
                            }
                        }
                    },
                    fh,
                    fmt=plistlib.FMT_BINARY,
                )

            expected = hashlib.sha256(header).hexdigest()
            self.assertEqual(patch_module._asar_header_hash(str(asar)), expected)

            patch_module.DRY_RUN = False
            with contextlib.redirect_stdout(io.StringIO()):
                patch_module.step_update_macos_asar_integrity(str(app))
            with info_plist.open("rb") as fh:
                updated = plistlib.load(fh)
            self.assertEqual(
                updated["ElectronAsarIntegrity"]["Resources/app.asar"]["hash"],
                expected,
            )

    def test_macos_signing_failure_stops_the_run(self):
        with loaded_patch_module() as patch_module:
            patch_module.DRY_RUN = False
            with (
                mock.patch.object(
                    patch_module,
                    "_macos_adhoc_entitlements",
                    return_value="/tmp/nonexistent-entitlements.plist",
                ),
                mock.patch.object(
                    patch_module,
                    "run_cmd",
                    side_effect=[(0, ""), (1, "codesign failed")],
                ),
                contextlib.redirect_stdout(io.StringIO()),
                self.assertRaises(SystemExit),
            ):
                patch_module.step_finish_macos("/tmp/Patched.app")

    def test_full_build_stops_before_packaging_when_js_patch_fails(self):
        with loaded_patch_module() as patch_module:
            patch_module.results = {
                "applied": ["partial"],
                "skipped": [],
                "failed": ["drifted target"],
            }

            with (
                contextlib.redirect_stdout(io.StringIO()) as output,
                self.assertRaises(SystemExit),
            ):
                patch_module._require_successful_js_patch()

            self.assertIn("已停止重打包与签名", output.getvalue())

    def test_macos_adhoc_entitlements_drop_restricted_team_values(self):
        with loaded_patch_module() as patch_module:
            original = {
                "com.apple.application-identifier": "TEAM.com.openai.codex",
                "com.apple.developer.team-identifier": "TEAM",
                "com.apple.security.application-groups": ["TEAM.group"],
                "keychain-access-groups": ["TEAM.*"],
                "com.apple.security.cs.allow-jit": True,
                "com.apple.security.automation.apple-events": True,
            }

            sanitized = patch_module._sanitize_macos_entitlements(original)

            self.assertNotIn("com.apple.application-identifier", sanitized)
            self.assertNotIn("com.apple.developer.team-identifier", sanitized)
            self.assertNotIn("com.apple.security.application-groups", sanitized)
            self.assertNotIn("keychain-access-groups", sanitized)
            self.assertTrue(sanitized["com.apple.security.cs.allow-jit"])
            self.assertTrue(
                sanitized["com.apple.security.cs.disable-library-validation"]
            )

    def test_browser_peer_authorization_fallback_is_narrow_and_idempotent(self):
        with (
            loaded_patch_module() as patch_module,
            tempfile.TemporaryDirectory() as tmp,
        ):
            main = Path(tmp) / "main-test.js"
            main.write_text(
                "const other={authorized:!1,"
                "reason:`untrusted-code-signing-identity`};"
                "const decoy=reason===`missing-code-signing-identity`?"
                "{authorized:!0}:other;"
                "function host(){return {onListening:e=>{"
                "(0,fsmod.chmodSync)(e,384),logger().info("
                "`node_repl_host_services_listening`,{})}}}"
                "function browser(){return {onListening:e=>{logger().info("
                "`browser-use native pipe listening`,{})}}}"
                "function authorizer(){return socket=>{let fd=getFd(socket);"
                "return fd==null?{authorized:!1,"
                "reason:`missing-socket-file-descriptor`}:"
                "addon.authorizeSocketPeer(fd,isDev)}}",
                encoding="utf-8",
            )
            patch_module.DRY_RUN = False
            patch_module.results = {"applied": [], "skipped": [], "failed": []}

            with contextlib.redirect_stdout(io.StringIO()):
                patch_module.apply_browser_peer_authorization_patch(str(main))
            first = main.read_text("utf-8")

            self.assertIn("addon.authorizeSocketPeer(fd,isDev)", first)
            self.assertIn("reason:`missing-socket-file-descriptor`", first)
            self.assertIn(
                "reason===`missing-code-signing-identity`?{authorized:!0}",
                first,
            )
            self.assertEqual(2, first.count("{authorized:!0}"))
            self.assertNotIn(
                "return fd==null?{authorized:!1,"
                "reason:`missing-socket-file-descriptor`}:"
                "addon.authorizeSocketPeer(fd,isDev)}",
                first,
            )
            self.assertIn("reason:`untrusted-code-signing-identity`", first)
            self.assertIn(
                "onListening:e=>{(0,fsmod.chmodSync)(e,384),logger().info("
                "`browser-use native pipe listening`",
                first,
            )
            self.assertEqual(2, first.count("(0,fsmod.chmodSync)(e,384)"))
            self.assertEqual([], patch_module.results["failed"])

            with contextlib.redirect_stdout(io.StringIO()):
                patch_module.apply_browser_peer_authorization_patch(str(main))
            self.assertEqual(first, main.read_text("utf-8"))
            self.assertEqual(1, len(patch_module.results["skipped"]))

    def test_browser_peer_authorization_rejects_duplicate_targets(self):
        with (
            loaded_patch_module() as patch_module,
            tempfile.TemporaryDirectory() as tmp,
        ):
            main = Path(tmp) / "main-test.js"
            authorizer = (
                "return socket=>{let fd=getFd(socket);return fd==null?"
                "{authorized:!1,reason:`missing-socket-file-descriptor`}:"
                "addon.authorizeSocketPeer(fd,isDev)}"
            )
            source = (
                "function host(){return {onListening:e=>{"
                "(0,fsmod.chmodSync)(e,384),logger().info("
                "`node_repl_host_services_listening`,{})}}}"
                "function browser(){return {onListening:e=>{logger().info("
                "`browser-use native pipe listening`,{})}}}"
                f"function first(){{{authorizer}}}"
                f"function second(){{{authorizer}}}"
            )
            main.write_text(source, encoding="utf-8")
            patch_module.DRY_RUN = False
            patch_module.results = {"applied": [], "skipped": [], "failed": []}

            with contextlib.redirect_stdout(io.StringIO()):
                patch_module.apply_browser_peer_authorization_patch(str(main))

            self.assertEqual(source, main.read_text("utf-8"))
            self.assertEqual([], patch_module.results["applied"])
            self.assertEqual(1, len(patch_module.results["failed"]))

    @staticmethod
    def run_patch(assets):
        return subprocess.run(
            [sys.executable, str(PATCH_SCRIPT), "--assets", str(assets)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )


if __name__ == "__main__":
    unittest.main()
