import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PATCH_SCRIPT = REPO_ROOT / "patch.py"


class ChatGptCodexPatchTests(unittest.TestCase):
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
            native_plugins.write_text(
                "function supported(method){return "
                "method!==`chatgpt`&&method!==`apikey`&&method!==`amazonBedrock`}",
                encoding="utf-8",
            )

            first = self.run_patch(assets)
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            self.assertNotIn("[FAIL]", first.stdout)
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
