import { expect, it } from "vitest";
import { INSTALL_CHARTS_PROPS } from "@/components/screens/nvke/NvkeOperatorInstallPlanChartsPanel.stories";
import {
  installPlanTreeRequest,
  installPlanTreeSchema,
  validateInstallPlanTree,
  type NvkeInstallPlanTree,
  type NvkeInstallPlanTreeForm,
} from "./operator-install-plan-tree";

const parent = INSTALL_CHARTS_PROPS.plan!,
  charts = INSTALL_CHARTS_PROPS.artifacts!;
const id = "87a76c80-3468-43aa-b2be-e511d587d312";
const row: NvkeInstallPlanTree = {
  id,
  version: 2,
  status: "prepared",
  planId: parent.id,
  planVersion: parent.version,
  operationId: parent.operationId,
  operationVersion: parent.operationVersion,
  siteId: parent.siteId,
  siteVersion: parent.siteVersion,
  authorityId: parent.authorityId,
  authorityVersion: parent.authorityVersion,
  planDigest: parent.plan.planDigest,
  chartArtifactId: charts.id,
  chartArtifactVersion: charts.version,
  chartArtifactDigest: charts.artifactDigest,
  treeDigest: "d".repeat(64),
  fileCount: 3,
  bytes: "9007199254740994",
  executionState: "requires-native-rendering-images-and-live-verification",
  continuationAuthorized: false,
};
const form: NvkeInstallPlanTreeForm = { artifactId: id, maximumBytes: row.bytes };
it("binds both prepared publications and preserves decimal precision", () => {
  expect(installPlanTreeRequest(form, parent, charts)).toEqual({
    format: 1,
    artifactId: id,
    planId: parent.id,
    planDigest: parent.plan.planDigest,
    chartArtifactId: charts.id,
    chartArtifactDigest: charts.artifactDigest,
    maximumBytes: "9007199254740994",
  });
  expect(validateInstallPlanTree(row, parent, charts, id, form)).toEqual(row);
});
it.each([
  { artifactId: parent.id },
  { artifactId: parent.operationId },
  { artifactId: charts.id },
  { maximumBytes: "0" },
  { maximumBytes: "01" },
  { maximumBytes: 9007199254740994 },
  { maximumBytes: "9223372036854775808" },
  { maximumBytes: "1" },
  { maximumBytes: "PRIVATE" },
  { timeoutSeconds: 300 },
  { allowPlainHttp: false },
  { maximumBytes: null },
])("refuses malformed or insufficient reviewed input: %j", (change) => {
  expect(() =>
    installPlanTreeRequest({ ...form, ...change } as NvkeInstallPlanTreeForm, parent, charts)
  ).toThrow();
});
it.each([
  { id: charts.id },
  { version: 0 },
  { status: "ready" },
  { planId: id },
  { planVersion: 99 },
  { planDigest: "a".repeat(64) },
  { operationId: id },
  { operationVersion: 99 },
  { siteId: id },
  { siteVersion: 99 },
  { authorityId: id },
  { authorityVersion: 99 },
  { chartArtifactId: id },
  { chartArtifactVersion: 99 },
  { chartArtifactDigest: "f".repeat(64) },
  { treeDigest: "PRIVATE" },
  { fileCount: 0 },
  { fileCount: 100001 },
  { fileCount: true },
  { bytes: 9007199254740994 },
  { bytes: "1" },
  { bytes: "0" },
  { bytes: "9223372036854775808" },
  { bytes: "PRIVATE" },
  { continuationAuthorized: true },
  { continuationAuthorized: null },
  { executionState: "ready" },
  { privatePath: "PRIVATE" },
  { __typename: "Other" },
])("refuses foreign, truncated or authorizing publications: %j", (change) => {
  expect(() => validateInstallPlanTree({ ...row, ...change }, parent, charts, id)).toThrow();
});
it("checks source publications, lower content bounds and the reviewed byte ceiling", () => {
  expect(installPlanTreeSchema.safeParse({ ...row, bytes: "PRIVATE" }).success).toBe(false);
  expect(() =>
    validateInstallPlanTree(row, parent, { ...charts, version: charts.version + 1 }, id)
  ).toThrow("TREE_SOURCE_CHANGED");
  expect(() => validateInstallPlanTree(row, parent, { ...charts, planVersion: 99 }, id)).toThrow(
    "CHART_SOURCE_CHANGED"
  );
  expect(() =>
    validateInstallPlanTree({ ...row, fileCount: 1 }, parent, { ...charts, chartCount: 2 }, id)
  ).toThrow();
  expect(() =>
    validateInstallPlanTree(row, parent, charts, id, { maximumBytes: charts.bytes })
  ).toThrow("TREE_BUDGET_EXCEEDED");
});
