import { test, expect } from "@playwright/test";
import { createServer, type Server } from "node:http";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import ts from "typescript";
let server: Server, origin: string;
const html = `<!doctype html><title>Native journal protocol qualification</title><script type="module">
import * as native from '/native-admission';
window.native=native;
window.calls=0;
window.makeOptions=(changed=false)=>({
 scope:{server:location.origin,organizationId:'123e4567-e89b-12d3-a456-426614174000',actorId:41},
 siteId:'123e4567-e89b-12d3-a456-426614174001',expectedSiteVersion:7,authorityVersion:3,
 source:{operationId:'123e4567-e89b-12d3-a456-426614174002',version:9,resourceKey:null},
 revision:'92c39b8ad563ebe5ef705f781b3038b901aba10f',
 values:{mode:'reset',timeoutSeconds:7200,review:{workflowId:'owner',baseRunId:'123e4567-e89b-12d3-a456-426614174003',workflowTaskEventId:'9007199254740993',activityEventId:'9007199254740994',activityType:'StartEtcdEncryptionRewrite',phase:'rewrite',mode:'restart_rewrite',rationale:changed?'PRIVATE_CHANGED':'PRIVATE_REASON',quiescenceEvidence:'PRIVATE_EVIDENCE',acknowledgeBoundaryReviewed:true,acknowledgeConflictingWorkStopped:true,retirementWorkflowId:''}},
 storage:localStorage,current:()=>true,sourceAvailable:()=>true,onRecord:()=>{},receipt:async()=>null,
 admit:async()=>{window.calls++;throw Error('UNEXPECTED_ADMISSION')}
});
</script>`;
test.beforeAll(async () => {
  server = createServer((req, res) => {
    const name = req.url?.slice(1);
    if (req.url === "/") {
      res.setHeader("Content-Type", "text/html");
      res.end(html);
      return;
    }
    if (!["native-admission", "admission", "recovery"].includes(name ?? "")) {
      res.writeHead(404);
      res.end();
      return;
    }
    const source = readFileSync(join(__dirname, "../lib/nvke", name + ".ts"), "utf8");
    res.setHeader("Content-Type", "application/javascript");
    res.end(
      ts.transpileModule(source, {
        compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
      }).outputText
    );
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  if (!address || typeof address === "string") throw Error("owned test server unavailable");
  origin = "http://127.0.0.1:" + address.port;
});
test.afterAll(async () => {
  await new Promise<void>((resolve, reject) =>
    server.close((err) => (err ? reject(err) : resolve()))
  );
});
test("serializes unknown native intent across real browser tabs without persisting review text", async ({
  context,
}) => {
  const first = await context.newPage(),
    second = await context.newPage();
  await Promise.all([first.goto(origin), second.goto(origin)]);
  for (const page of [first, second])
    await page.waitForFunction(() => Object.hasOwn(window, "makeOptions"));
  // These functions run only in the owned test origin; the application modules are served unchanged.
  await first.evaluate(`{
  const options=makeOptions();
  options.admit=async()=>{calls++;window.sending=true;await new Promise(resolve=>window.release=resolve);throw Error('LOST_REPLY')};
  window.attempt=native.admitNvkeNative(options).then(()=>null,error=>error.code||error.message);
 void 0;
 }`);
  await first.waitForFunction(`window.sending===true`);
  await second.evaluate(
    `void(window.attempt=native.admitNvkeNative(makeOptions(true)).then(()=>null,error=>error.code||error.message))`
  );
  await expect
    .poll(() =>
      second.evaluate(async () => {
        const locks = await navigator.locks.query();
        return locks.pending?.length;
      })
    )
    .toBe(1);
  const stored = await second.evaluate(() => JSON.stringify(Object.values(localStorage)));
  expect(stored).not.toContain("PRIVATE");
  expect(JSON.parse(JSON.parse(stored)[0])).toMatchObject({
    format: 4,
    mode: "reset",
    authorityVersion: 3,
  });
  await first.evaluate(`window.release()`);
  expect(await first.evaluate(`window.attempt`)).toBe("LOST_REPLY");
  expect(await second.evaluate(`window.attempt`)).toBe("RECOVERY_MISMATCH");
  expect(await second.evaluate(`window.calls`)).toBe(0);
  expect(await first.evaluate(`window.calls`)).toBe(1);
  const recovered = await second.evaluate(`(async()=>{
  const record=JSON.parse(Object.values(localStorage)[0]);
  const options=makeOptions(true);delete options.values;
  options.receipt=async()=>({id:'123e4567-e89b-12d3-a456-426614174004',siteId:record.siteId,requestId:record.requestId,operationKey:record.operationKey,isInventory:false});
  return await native.recoverNvkeNative(options);
 })()`);
  expect(recovered).toMatchObject({ operationKey: "temporal.reset.RotateEtcdEncryptionKey" });
  expect(await second.evaluate(`window.calls`)).toBe(0);
  expect(
    await second.evaluate(() => Object.keys(localStorage).some((key) => !key.includes(":receipt:")))
  ).toBe(false);
  expect(await second.evaluate(() => JSON.stringify(Object.values(localStorage)))).not.toContain(
    "PRIVATE"
  );
});
