import { expect, it } from "vitest";
import { INSTALL_WORKLOAD_PROPS as fixture } from "@/components/screens/nvke/NvkeOperatorInstallPlanWorkloadPanel.stories";
import {
  installPlanWorkloadRequest,
  parseWorkloadContextBindings,
  validateInstallPlanWorkload,
} from "./operator-install-plan-workload";
const plan = fixture.plan!,
  charts = fixture.charts!,
  tree = fixture.tree!,
  row = fixture.artifacts!;
const contextId = "3636227e-50df-4c08-b7cf-245b72d0b41d";
const bindings = { nktp: { contextId, contextDigest: "a".repeat(64) } };
const form = {
  artifactId: row.id,
  contextBindings: JSON.stringify(bindings),
  maximumBytes: "67108864",
  maximumResources: "10000",
  timeoutSeconds: "300",
};
it("binds every context to the reviewed source and bounds the whole observation", () => {
  expect(installPlanWorkloadRequest(form, plan, charts, tree)).toEqual({
    format: 1,
    artifactId: row.id,
    planId: plan.id,
    planDigest: plan.plan.planDigest,
    treeId: tree.id,
    treeDigest: tree.treeDigest,
    contextBindings: bindings,
    maximumBytes: "67108864",
    maximumResources: 10000,
    timeoutSeconds: 300,
  });
  expect(validateInstallPlanWorkload(row, plan, charts, tree, row.id, form)).toEqual(row);
  expect(
    installPlanWorkloadRequest({ ...form, contextBindings: "{}" }, plan, charts, tree)
      .contextBindings
  ).toEqual({});
});
it.each([
  '{"nktp":{"contextId":"' +
    contextId +
    '","contextId":"' +
    contextId +
    '","contextDigest":"' +
    "a".repeat(64) +
    '"}}',
  '{"nktp":{"contextId":"' +
    contextId +
    '","contextDigest":"' +
    "a".repeat(64) +
    '"},"nktp":{"contextId":"' +
    contextId +
    '","contextDigest":"' +
    "a".repeat(64) +
    '"}}',
  '{"nktp":{"contextId":"' +
    contextId +
    '","contextDigest":"' +
    "a".repeat(64) +
    '","context\\u0049d":"' +
    contextId +
    '"}}',
  JSON.stringify({ nktp: { ContextId: contextId, contextDigest: "a".repeat(64) } }),
  JSON.stringify({ nktp: { contextId, contextDigest: "a".repeat(64), helper: "/private" } }),
  JSON.stringify({ "../private": bindings.nktp }),
  "null",
  "[]",
  "false",
  "{}{}",
  '{"nktp":null}',
])("refuses ambiguous or private context bindings before transport: %s", (contextBindings) => {
  expect(() =>
    installPlanWorkloadRequest({ ...form, contextBindings }, plan, charts, tree)
  ).toThrow();
});
it.each([
  { artifactId: plan.id },
  { artifactId: charts.id },
  { artifactId: tree.id },
  { artifactId: plan.operationId },
  { contextBindings: JSON.stringify({ a: bindings.nktp, b: bindings.nktp }) },
  {
    contextBindings: JSON.stringify({ nktp: { contextId: row.id, contextDigest: "a".repeat(64) } }),
  },
  { maximumBytes: "67108865" },
  { maximumBytes: "01" },
  { maximumResources: "10001" },
  { maximumResources: "0" },
  { timeoutSeconds: "301" },
  { credentials: "PRIVATE" },
])("refuses invalid workload input: %j", (change) => {
  expect(() => installPlanWorkloadRequest({ ...form, ...change }, plan, charts, tree)).toThrow();
});
it.each([
  { treeVersion: tree.version + 1 },
  { planVersion: plan.version + 1 },
  { workloadDigest: "invalid" },
  { imageUseCount: undefined },
  { imageReferenceCount: row.imageUseCount + 1 },
  { itemCount: plan.itemCount + 1 },
  { resourceCount: 10001 },
  { contextBindings: bindings },
  { continuationAuthorized: true },
  { executionState: "ready" },
])("refuses stale, private or authorizing summaries: %j", (change) => {
  expect(() =>
    validateInstallPlanWorkload({ ...row, ...change }, plan, charts, tree, row.id)
  ).toThrow();
});
it("refuses a source change or resource budget overrun", () => {
  expect(() =>
    validateInstallPlanWorkload(row, plan, charts, { ...tree, version: tree.version + 1 }, row.id)
  ).toThrow();
  expect(() =>
    validateInstallPlanWorkload(row, plan, charts, tree, row.id, { maximumResources: "1" })
  ).toThrow();
});
it("parses escaped keys and refuses escaped duplicate names", () => {
  expect(
    parseWorkloadContextBindings(
      '{"nk\\u0074p":{"contextId":"' + contextId + '","contextDigest":"' + "a".repeat(64) + '"}}'
    )
  ).toEqual(bindings);
});

it("keeps recovery intent stable when JSON key order changes", () => {
  const another = {
    contextId: "06d48a1d-de2f-4a26-9ba9-bd16e94973d0",
    contextDigest: "b".repeat(64),
  };
  const left = installPlanWorkloadRequest(
    { ...form, contextBindings: JSON.stringify({ zeta: bindings.nktp, alpha: another }) },
    plan,
    charts,
    tree
  );
  const right = installPlanWorkloadRequest(
    {
      ...form,
      contextBindings: JSON.stringify({
        alpha: { contextDigest: another.contextDigest, contextId: another.contextId },
        zeta: bindings.nktp,
      }),
    },
    plan,
    charts,
    tree
  );
  expect(JSON.stringify(left)).toBe(JSON.stringify(right));
});
