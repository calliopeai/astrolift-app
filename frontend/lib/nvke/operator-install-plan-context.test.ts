import { expect, it } from "vitest";
import { INSTALL_TREE_PROPS } from "@/components/screens/nvke/NvkeOperatorInstallPlanTreePanel.stories";
import {
  installPlanContextFormSchema,
  installPlanContextRequest,
  validateInstallPlanContext,
  type NvkeInstallPlanContext,
  type NvkeInstallPlanContextForm,
} from "./operator-install-plan-context";

const plan = INSTALL_TREE_PROPS.plan!,
  charts = INSTALL_TREE_PROPS.charts!,
  tree = INSTALL_TREE_PROPS.artifacts!;
const id = "590ad665-6588-43da-9614-09e01fbc4a01";
const selected = { item: "nktp", ordinal: 1 };
const form: NvkeInstallPlanContextForm = {
  artifactId: id,
  item: selected.item,
  ordinal: "1",
  revision: "9007199254740993",
  upgrade: true,
  maximumBytes: "16777216",
  maximumArtifactBytes: "1048576",
  maximumCalls: "512",
  maximumIterations: "64",
  timeoutSeconds: "300",
};
const row: NvkeInstallPlanContext = {
  id,
  version: 2,
  status: "prepared",
  planId: plan.id,
  planVersion: plan.version,
  planDigest: plan.plan.planDigest,
  chartArtifactId: charts.id,
  chartArtifactVersion: charts.version,
  chartArtifactDigest: charts.artifactDigest,
  treeId: tree.id,
  treeVersion: tree.version,
  treeDigest: tree.treeDigest,
  operationId: plan.operationId,
  operationVersion: plan.operationVersion,
  siteId: plan.siteId,
  siteVersion: plan.siteVersion,
  authorityId: plan.authorityId,
  authorityVersion: plan.authorityVersion,
  ...selected,
  captureDigest: "1".repeat(64),
  frozenChartSha256: "2".repeat(64),
  contextDigest: "3".repeat(64),
  documentCount: 1,
  lookupCount: 0,
  observedAt: "2026-11-09T16:00:00+00:00",
  executionState: "requires-authenticated-phase-verification-images-and-linked-admission",
  continuationAuthorized: false,
};

it("binds every publication and exact item while retaining native revision precision", () => {
  expect(installPlanContextRequest(form, plan, charts, tree)).toEqual({
    format: 1,
    artifactId: id,
    planId: plan.id,
    planDigest: plan.plan.planDigest,
    treeId: tree.id,
    treeDigest: tree.treeDigest,
    item: "nktp",
    revision: "9007199254740993",
    upgrade: true,
    maximumBytes: "16777216",
    maximumArtifactBytes: "1048576",
    maximumCalls: 512,
    maximumIterations: 64,
    timeoutSeconds: 300,
  });
  expect(validateInstallPlanContext(row, plan, charts, tree, id, selected)).toEqual(row);
  expect(
    installPlanContextRequest({ ...form, revision: "1", upgrade: false }, plan, charts, tree)
      .revision
  ).toBe("1");
});

it.each([
  { artifactId: plan.id },
  { artifactId: plan.operationId },
  { artifactId: charts.id },
  { artifactId: tree.id },
  { item: "../private" },
  { item: "nktp\n" },
  { ordinal: "01" },
  { ordinal: "-1" },
  { ordinal: String(plan.itemCount) },
  { revision: 9007199254740993 },
  { revision: "9223372036854775808" },
  { revision: "0" },
  { revision: "01" },
  { revision: "1\n" },
  { upgrade: false },
  { upgrade: null },
  { maximumBytes: "0" },
  { maximumBytes: "536870913" },
  { maximumBytes: 123 },
  { maximumArtifactBytes: "65535" },
  { maximumArtifactBytes: "134217729" },
  { maximumCalls: "2049" },
  { maximumCalls: "1e3" },
  { maximumIterations: "65" },
  { timeoutSeconds: "301" },
  { helperProgram: "PRIVATE" },
  { credentials: "PRIVATE" },
])("refuses malformed or cross-kind preparation input %#", (change) => {
  expect(() => installPlanContextRequest({ ...form, ...change }, plan, charts, tree)).toThrow();
});

it.each(["NVKE_phase-1", "A".repeat(128)])("preserves native item names %s", (item) => {
  expect(installPlanContextFormSchema.parse({ ...form, item }).item).toBe(item);
});

it.each([
  { id: plan.id },
  { planVersion: plan.version + 1 },
  { chartArtifactVersion: charts.version + 1 },
  { treeVersion: tree.version + 1 },
  { treeDigest: "f".repeat(64) },
  { operationVersion: plan.operationVersion + 1 },
  { authorityVersion: plan.authorityVersion + 1 },
  { item: "controller" },
  { ordinal: 2 },
  { ordinal: true },
  { captureDigest: "PRIVATE" },
  { captureDigest: "1".repeat(64) + "\n" },
  { frozenChartSha256: null },
  { contextDigest: "PRIVATE" },
  { documentCount: -1 },
  { lookupCount: 2049 },
  { observedAt: "yesterday" },
  { observedAt: "2026-11-09T16:00:00+00:00\n" },
  { observedAt: "2026-11-09T16:00:00" },
  { executionState: "ready" },
  { continuationAuthorized: true },
  { capture: "PRIVATE" },
])("hides private, stale or authorizing replies %#", (change) => {
  expect(() =>
    validateInstallPlanContext({ ...row, ...change }, plan, charts, tree, id, selected)
  ).toThrow();
});

it("invalidates results when the reviewed tree or selected original ordinal changes", () => {
  expect(() =>
    validateInstallPlanContext(
      row,
      plan,
      charts,
      { ...tree, version: tree.version + 1 },
      id,
      selected
    )
  ).toThrow();
  expect(() =>
    validateInstallPlanContext(row, plan, charts, tree, id, { ...selected, ordinal: 0 })
  ).toThrow();
});
