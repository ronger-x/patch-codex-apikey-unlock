import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PATCH_SCRIPT = REPO_ROOT / "patch.py"


class ChatGPTCodexPatchTests(unittest.TestCase):
    def test_26707_model_and_fast_gates_are_patched_idempotently(self):
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
            model_filter.write_text(
                "function filter({authMethod,allowed,models,useHiddenModels}){"
                "models.forEach(item=>{"
                "if(useHiddenModels?allowed.has(item.model):!item.hidden){show(item)}"
                "})}",
                encoding="utf-8",
            )
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
                "if(authMethod===`apikey`||"
                "(useHiddenModels?allowed.has(item.model):!item.hidden))",
                model_filter.read_text("utf-8"),
            )

            contents_after_first_run = {
                path.name: path.read_text("utf-8") for path in assets.glob("*.js")
            }
            second = self.run_patch(assets)
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            self.assertNotIn("[FAIL]", second.stdout)
            self.assertIn("[SKIP] 服务层级授权门控", second.stdout)
            self.assertIn("[SKIP] Fast 请求服务层级门控", second.stdout)
            self.assertIn("[SKIP] 隐藏模型列表解锁", second.stdout)
            self.assertEqual(
                contents_after_first_run,
                {path.name: path.read_text("utf-8") for path in assets.glob("*.js")},
            )

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
