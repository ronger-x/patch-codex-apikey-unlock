import contextlib
import io
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_patch import loaded_patch_module


GUEST_ORIGINAL = "?{settings:{precise_location_allowed:!0}}:await"
GUEST_PATCHED = "?{settings:{precise_location_allowed:!1}}:await"
MAPPING_ORIGINAL = "preciseLocationAllowed:w.settings?.precise_location_allowed"
MAPPING_PATCHED = MAPPING_ORIGINAL + "??!1"
OPT_IN = (
    "async function optIn(permission,update){"
    "if(await permission()!==\"granted\")return!1;"
    "return update({precise_location_allowed:!0})}"
)
LOCATION_SOURCE = (
    "const queryKey=[\"chatgpt-user-settings\"];"
    "let remote={},queries=0;"
    "async function query(path){"
    "if(path!==\"/settings/user\")throw Error(path);queries++;return remote}"
    "async function q(guest){let w=guest"
    + GUEST_ORIGINAL
    + " query(\"/settings/user\");return {"
    + MAPPING_ORIGINAL
    + "}}"
    + OPT_IN
)


class LocationPrivacyTests(unittest.TestCase):
    @staticmethod
    def reset_results(patch, dry_run=False):
        patch.DRY_RUN = dry_run
        patch.results = {"applied": [], "skipped": [], "failed": []}

    @staticmethod
    def write_fixture(directory, name, source=LOCATION_SOURCE):
        path = (Path(directory) / name).resolve()
        path.write_text(source, encoding="utf-8", newline="\n")
        return str(path)

    @staticmethod
    def apply_location_patch(patch, contents):
        with contextlib.redirect_stdout(io.StringIO()):
            return patch._patch_precise_location_defaults(contents)

    def assert_defaults_patched(self, source):
        self.assertIn(GUEST_PATCHED, source)
        self.assertNotIn(GUEST_ORIGINAL, source)
        self.assertIn(MAPPING_PATCHED + "}", source)
        self.assertNotIn(MAPPING_ORIGINAL + "}", source)
        self.assertIn(OPT_IN, source)

    def test_every_emitted_copy_is_patched_and_second_run_is_unchanged(self):
        with loaded_patch_module() as patch, tempfile.TemporaryDirectory() as tmp:
            contents = {
                self.write_fixture(tmp, name): LOCATION_SOURCE
                for name in ("104.abcd.js", "207.ef01.js")
            }
            self.reset_results(patch)

            self.assertTrue(self.apply_location_patch(patch, contents))

            for path, source in contents.items():
                self.assert_defaults_patched(source)
                self.assertEqual(source, Path(path).read_text("utf-8"))
            self.assertEqual(2, len(patch.results["applied"]))
            self.assertEqual([], patch.results["failed"])
            after_first_run = dict(contents)
            self.reset_results(patch)

            self.assertTrue(self.apply_location_patch(patch, contents))

            self.assertEqual(after_first_run, contents)
            self.assertEqual([], patch.results["applied"])
            self.assertEqual(2, len(patch.results["skipped"]))
            self.assertEqual([], patch.results["failed"])

    def test_already_patched_copy_is_satisfied_without_writing(self):
        source = LOCATION_SOURCE.replace(GUEST_ORIGINAL, GUEST_PATCHED).replace(
            MAPPING_ORIGINAL, MAPPING_PATCHED
        )
        with loaded_patch_module() as patch, tempfile.TemporaryDirectory() as tmp:
            path = self.write_fixture(tmp, "104.abcd.js", source)
            contents = {path: source}
            self.reset_results(patch)

            self.assertTrue(self.apply_location_patch(patch, contents))

            self.assertEqual(source, contents[path])
            self.assertEqual(source, Path(path).read_text("utf-8"))
            self.assertEqual([], patch.results["applied"])
            self.assertEqual(1, len(patch.results["skipped"]))
            self.assertEqual([], patch.results["failed"])

    def test_dry_run_leaves_files_unchanged(self):
        with loaded_patch_module() as patch, tempfile.TemporaryDirectory() as tmp:
            path = self.write_fixture(tmp, "104.abcd.js")
            contents = {path: LOCATION_SOURCE}
            self.reset_results(patch, dry_run=True)

            self.assertTrue(self.apply_location_patch(patch, contents))

            self.assertEqual(LOCATION_SOURCE, Path(path).read_text("utf-8"))
            self.assert_defaults_patched(contents[path])
            self.assertEqual(1, len(patch.results["applied"]))
            self.assertEqual([], patch.results["failed"])

    def test_drift_or_duplicate_candidates_fail_before_any_write(self):
        malformed_sources = {
            "query-marker-drift": LOCATION_SOURCE.replace(
                "chatgpt-user-settings", "renamed-user-settings"
            ),
            "endpoint-drift": LOCATION_SOURCE.replace(
                "/settings/user", "/settings/profile"
            ),
            "guest-drift": LOCATION_SOURCE.replace(
                GUEST_ORIGINAL,
                "?{settings:{precise_location_allowed:true}}:await",
            ),
            "mapping-drift": LOCATION_SOURCE.replace(
                MAPPING_ORIGINAL,
                "preciseLocationAllowed:w.settings.precise_location_allowed",
            ),
            "duplicate-guest": LOCATION_SOURCE
            + "async function duplicate(guest){return guest"
            + GUEST_ORIGINAL
            + " query(\"/settings/user\")}",
            "duplicate-mapping": LOCATION_SOURCE
            + "function duplicate(w){return {"
            + MAPPING_ORIGINAL
            + "}}",
        }
        with loaded_patch_module() as patch, tempfile.TemporaryDirectory() as tmp:
            for case, malformed in malformed_sources.items():
                with self.subTest(case=case):
                    good_path = self.write_fixture(tmp, "104.abcd.js")
                    bad_path = self.write_fixture(tmp, "999.ef01.js", malformed)
                    before = {good_path: LOCATION_SOURCE, bad_path: malformed}
                    contents = dict(before)
                    self.reset_results(patch)

                    self.assertFalse(self.apply_location_patch(patch, contents))

                    self.assertEqual(before, contents)
                    for path, source in before.items():
                        self.assertEqual(source, Path(path).read_text("utf-8"))
                    self.assertEqual([], patch.results["applied"])
                    self.assertEqual([], patch.results["skipped"])
                    self.assertEqual(1, len(patch.results["failed"]))

    def test_absent_location_feature_is_skipped(self):
        with loaded_patch_module() as patch, tempfile.TemporaryDirectory() as tmp:
            source = 'const queryKey=["chatgpt-user-settings"];'
            path = self.write_fixture(tmp, "104.abcd.js", source)
            contents = {path: source}
            self.reset_results(patch)

            self.assertTrue(self.apply_location_patch(patch, contents))

            self.assertEqual({path: source}, contents)
            self.assertEqual(source, Path(path).read_text("utf-8"))
            self.assertEqual([], patch.results["applied"])
            self.assertEqual(1, len(patch.results["skipped"]))
            self.assertEqual([], patch.results["failed"])

    @unittest.skipUnless(shutil.which("node"), "Node.js is required")
    def test_runtime_preserves_explicit_preferences_and_opt_in(self):
        with loaded_patch_module() as patch, tempfile.TemporaryDirectory() as tmp:
            path = self.write_fixture(tmp, "104.abcd.js")
            contents = {path: LOCATION_SOURCE}
            self.reset_results(patch)
            self.assertTrue(self.apply_location_patch(patch, contents))
            self.assertIn(OPT_IN, contents[path])
            runtime = contents[path] + (
                ";(async()=>{let rows=[];"
                "for(let [name,guest,response] of ["
                '["guest",true,{settings:{precise_location_allowed:true}}],'
                '["remote-true",false,{settings:{precise_location_allowed:true}}],'
                '["remote-false",false,{settings:{precise_location_allowed:false}}],'
                '["missing-field",false,{settings:{}}],'
                '["missing-settings",false,{}],'
                '["null-settings",false,{settings:null}]]){'
                "remote=response;queries=0;let value=(await q(guest))"
                ".preciseLocationAllowed;rows.push({name,value,queries})}"
                "let writes=[];let denied=await optIn(async()=>\"denied\","
                "async value=>writes.push(value));let granted=await optIn("
                "async()=>\"granted\",async value=>{writes.push(value);return true});"
                "console.log(JSON.stringify({rows,denied,granted,writes}))"
                "})().catch(error=>{console.error(error);process.exitCode=1})"
            )
            result = subprocess.run(
                [shutil.which("node"), "-e", runtime],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            observed = json.loads(result.stdout)
            self.assertEqual(
                [
                    {"name": "guest", "value": False, "queries": 0},
                    {"name": "remote-true", "value": True, "queries": 1},
                    {"name": "remote-false", "value": False, "queries": 1},
                    {"name": "missing-field", "value": False, "queries": 1},
                    {"name": "missing-settings", "value": False, "queries": 1},
                    {"name": "null-settings", "value": False, "queries": 1},
                ],
                observed["rows"],
            )
            self.assertFalse(observed["denied"])
            self.assertTrue(observed["granted"])
            self.assertEqual([{"precise_location_allowed": True}], observed["writes"])


if __name__ == "__main__":
    unittest.main()
