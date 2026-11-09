import { expect, it } from "vitest";
import { INSTALL_CHARTS_PROPS } from "@/components/screens/nvke/NvkeOperatorInstallPlanChartsPanel.stories";
import {
  installPlanChartsFormSchema,
  installPlanChartsRequest,
  installPlanChartsSchema,
  validateInstallPlanCharts,
  type NvkeInstallPlanChartsForm,
} from "./operator-install-plan-charts";

const parent = INSTALL_CHARTS_PROPS.plan!,
  row = INSTALL_CHARTS_PROPS.artifacts!;
const form: NvkeInstallPlanChartsForm = {
  artifactId: row.id,
  maximumBytes: row.bytes,
  maximumExpandedBytes: row.expandedBytes,
  timeoutSeconds: "300",
  allowPlainHttp: false,
};
it("preserves int64 byte strings and binds all chart options to the exact parent", () => {
  expect(installPlanChartsRequest(form, parent)).toEqual({
    format: 1,
    artifactId: row.id,
    planId: parent.id,
    planDigest: parent.plan.planDigest,
    maximumBytes: "9007199254740993",
    maximumExpandedBytes: "9223372036854775807",
    timeoutSeconds: 300,
    allowPlainHttp: false,
  });
  expect(validateInstallPlanCharts(row, parent, row.id, form)).toEqual(row);
  expect(
    validateInstallPlanCharts(
      { ...row, __typename: "NvkeOperatorInstallPlanChartsType" },
      parent,
      row.id
    ).__typename
  ).toBe("NvkeOperatorInstallPlanChartsType");
});
it.each([
  { artifactId: parent.id },
  { artifactId: parent.operationId },
  { maximumBytes: "0" },
  { maximumBytes: "01" },
  { maximumBytes: 9007199254740993 },
  { maximumExpandedBytes: "9223372036854775808" },
  { maximumExpandedBytes: "PRIVATE" },
  { timeoutSeconds: "0" },
  { timeoutSeconds: "3601" },
  { timeoutSeconds: "1.0" },
  { timeoutSeconds: 300 },
  { allowPlainHttp: "false" },
  { credentials: "PRIVATE" },
])("refuses invalid or ambiguous options before producing a request: %j", (change) => {
  expect(() =>
    installPlanChartsRequest({ ...form, ...change } as NvkeInstallPlanChartsForm, parent)
  ).toThrow();
});
it("supports the exact deadline boundaries and requires an explicit boolean HTTP policy", () => {
  expect(
    installPlanChartsRequest({ ...form, timeoutSeconds: "1", allowPlainHttp: true }, parent)
      .timeoutSeconds
  ).toBe(1);
  expect(installPlanChartsRequest({ ...form, timeoutSeconds: "3600" }, parent).timeoutSeconds).toBe(
    3600
  );
  expect(installPlanChartsFormSchema.safeParse({ ...form, allowPlainHttp: null }).success).toBe(
    false
  );
});
it.each([
  { id: parent.id },
  { version: 0 },
  { status: "ready" },
  { planId: row.id },
  { planVersion: row.planVersion + 1 },
  { planDigest: "a".repeat(64) },
  { operationId: row.id },
  { operationVersion: row.operationVersion + 1 },
  { siteId: row.id },
  { siteVersion: row.siteVersion + 1 },
  { authorityId: row.id },
  { authorityVersion: row.authorityVersion + 1 },
  { artifactDigest: "PRIVATE" },
  { chartCount: parent.itemCount + 1 },
  { chartCount: true },
  { bytes: 9007199254740993 },
  { bytes: "0" },
  { expandedBytes: "9223372036854775808" },
  { continuationAuthorized: true },
  { continuationAuthorized: 0 },
  { executionState: "ready" },
  { privatePath: "PRIVATE" },
  { __typename: "OtherType" },
])("rejects foreign, malformed, private or authorizing results: %j", (change) => {
  expect(() => validateInstallPlanCharts({ ...row, ...change }, parent, row.id)).toThrow();
});
it("does not throw from safe parsing malformed decimal values and rejects reviewed budget overruns", () => {
  expect(installPlanChartsSchema.safeParse({ ...row, bytes: "PRIVATE" }).success).toBe(false);
  expect(() =>
    validateInstallPlanCharts(row, parent, row.id, { ...form, maximumBytes: "1" })
  ).toThrow("CHART_BUDGET_EXCEEDED");
});
it("keeps an empty chart stage explicit and non-authorizing", () => {
  const result = validateInstallPlanCharts(
    { ...row, chartCount: 0, bytes: "0", expandedBytes: "0" },
    parent,
    row.id
  );
  expect(result.chartCount).toBe(0);
  expect(result.continuationAuthorized).toBe(false);
});
