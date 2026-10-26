# -*- coding: utf-8 -*-
import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_patch import loaded_patch_module


class SavedServiceTierTests(unittest.TestCase):
    def test_saved_tier_auth_preserves_server_model_and_copilot_checks(self):
        source = (
            "let reads=0;"
            "async function resolve(client,logger,model,allowed){"
            "if(!await allowed())return null;reads++;"
            "return client.fast&&model.supported?`fast`:null}"
            "function reader(client,{runtime},ready){return{"
            "async readServiceTier(model){return ready(),"
            "resolve(client.requestClient,client.logger,model,async()=>{"
            "if(client.getHostId()===`local`&&await runtime.isUsingCopilotInference())"
            "return!1;let account=await client.getAccount({priority:`critical`});"
            "return account.account?.type===`chatgpt`||"
            "account.account?.type===`amazonBedrock`})}}}"
            "function unrelated(account){return account.account?.type===`chatgpt`||"
            "account.account?.type===`amazonBedrock`}"
            "let rows=[];"
            "for(let type of [null,`apiKey`,`chatgpt`,`amazonBedrock`,`unknown`])"
            "for(let required of [false,true,undefined])"
            "for(let copilot of [false,true])"
            "for(let fast of [false,true])"
            "for(let supported of [false,true]){"
            "reads=0;let response={account:type==null?null:{type},"
            "requiresOpenaiAuth:required},"
            "api=reader({requestClient:{fast},logger:{},"
            "getHostId:()=>`local`,getAccount:async()=>response},"
            "{runtime:{isUsingCopilotInference:async()=>copilot}},()=>{});"
            "rows.push({type,required:String(required),copilot,fast,supported,"
            "value:await api.readServiceTier({supported}),reads,"
            "unrelated:unrelated(response)})}"
            "console.log(JSON.stringify(rows))"
        )
        with loaded_patch_module() as patch, tempfile.TemporaryDirectory() as tmp:
            assets = Path(tmp)
            target = assets / "renamed-bundle.mjs"
            # The patcher scans JS; Node executes the ESM copy below.
            js = assets / "renamed-bundle.js"
            js.write_text(source, encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                patch.DRY_RUN = True
                patch.apply_saved_service_tier_auth_patch(str(assets))
                self.assertEqual(source, js.read_text("utf-8"))
                patch.DRY_RUN = False
                patch.apply_saved_service_tier_auth_patch(str(assets))
            target.write_bytes(js.read_bytes())
            result = subprocess.run(["node", str(target)], capture_output=True,
                                    text=True, check=True, timeout=10)
            for row in json.loads(result.stdout):
                with self.subTest(row=row):
                    authorized = not row["copilot"] and (
                        row["type"] in ("apiKey", "chatgpt", "amazonBedrock") or
                        row["type"] is None and row["required"] == "false")
                    self.assertEqual(int(authorized), row["reads"])
                    self.assertEqual("fast" if authorized and row["fast"] and
                                     row["supported"] else None, row["value"])
                    self.assertEqual(row["type"] in ("chatgpt", "amazonBedrock"),
                                     row["unrelated"])
            patched = js.read_bytes()
            with contextlib.redirect_stdout(io.StringIO()):
                patch.apply_saved_service_tier_auth_patch(str(assets))
            self.assertEqual(patched, js.read_bytes())
            for ambiguous in (False, True):
                with self.subTest(ambiguous=ambiguous):
                    changed = source if ambiguous else source.replace(
                        "priority:`critical`", "priority:`background`")
                    js.write_text(changed, encoding="utf-8")
                    if ambiguous:
                        (assets / "duplicate.js").write_text(source, encoding="utf-8")
                    failures = len(patch.results["failed"])
                    with contextlib.redirect_stdout(io.StringIO()):
                        patch.apply_saved_service_tier_auth_patch(str(assets))
                    self.assertEqual(failures + 1, len(patch.results["failed"]))
                    self.assertEqual(changed, js.read_text("utf-8"))
