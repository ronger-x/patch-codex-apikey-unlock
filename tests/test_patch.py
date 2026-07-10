import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PATCH_SCRIPT = REPO_ROOT / "patch.py"


class ChatGPTCodexPatchTests(unittest.TestCase):
    def test_26707_model_reasoning_and_fast_gates_are_patched_idempotently(self):
        with tempfile.TemporaryDirectory() as tmp:
            assets = Path(tmp)
            service_tier = assets / "use-service-tier-settings-test.js"
            request_tier = assets / "read-service-tier-for-request-test.js"
            model_filter = assets / "model-list-filter-test.js"
            native_plugins = assets / "use-plugins-test.js"

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
