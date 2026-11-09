import { expect, it } from "vitest";
import { INSTALL_PLAN_PROPS } from "@/components/screens/nvke/NvkeOperatorInstallPlanPanel.stories";
import {
  installPlanSchema,
  installPlanMatches,
  installPlanFormSchema,
  validateInstallPlanPage,
} from "./operator-install-plan";
const plan = INSTALL_PLAN_PROPS.plan!;
const items = INSTALL_PLAN_PROPS.itemTable.rows;
const page = {
  planId: plan.id,
  planDigest: plan.plan.planDigest,
  view: "items",
  items,
  totalCount: 3,
  nextCursor: null,
};
it("accepts the typed plan without losing int64 bytes and strips private extras", () => {
  const result = installPlanSchema.parse({ ...plan, requestText: "private" });
  expect(result).toEqual(plan);
  expect(result.retainedBytes).toBe("9007199254740993");
  expect(
    installPlanSchema.parse({ ...plan, retainedBytes: "9223372036854775807" }).retainedBytes
  ).toBe("9223372036854775807");
});
it.each(["01", "-1", "1e3", "9223372036854775808"])("refuses invalid byte count %s", (value) => {
  expect(installPlanSchema.safeParse({ ...plan, proposedBytes: value }).success).toBe(false);
});
it.each([
  { ...plan, status: "preparing" },
  { ...plan, plan: { ...plan.plan, continuationAuthorized: true } },
  { ...plan, plan: { ...plan.plan, requiresExplicitAcknowledgement: false } },
  { ...plan, plan: { ...plan.plan, requiresOriginalSourceVerification: false } },
  { ...plan, plan: { ...plan.plan, firstRemainingOrdinal: plan.itemCount } },
  { ...plan, plan: { ...plan.plan, id: plan.operationId } },
  { ...plan, plan: { ...plan.plan, source: { ...plan.plan.source, nativeExitCode: 0 } } },
  { ...plan, plan: { ...plan.plan, source: { ...plan.plan.source, jobId: plan.id } } },
  {
    ...plan,
    plan: { ...plan.plan, source: { ...plan.plan.source, originalDeploymentEvidence: "verified" } },
  },
  { ...plan, plan: { ...plan.plan, preparedAt: "2020-01-01T00:00:00Z" } },
  { ...plan, infrastructure: { ...plan.infrastructure, observedAt: "2020-01-01T00:00:00Z" } },
  { ...plan, infrastructure: { ...plan.infrastructure, apiReady: false } },
])("refuses malformed, contradictory or authorizing headers %#", (value) => {
  expect(installPlanSchema.safeParse(value).success).toBe(false);
});
it("binds all current source dimensions", () => {
  const source = {
    siteId: plan.siteId,
    siteVersion: plan.siteVersion,
    operationId: plan.operationId,
    operationVersion: plan.operationVersion,
    authorityId: plan.authorityId,
    authorityVersion: plan.authorityVersion,
    instanceId: plan.plan.source.instanceId,
    bundleManifestSha256: plan.plan.source.bundleManifestSha256,
    nativeRevision: plan.plan.source.nativeRevision,
  };
  expect(installPlanMatches(plan, source)).toBe(true);
  for (const key of Object.keys(source) as (keyof typeof source)[]) {
    expect(
      installPlanMatches(plan, {
        ...source,
        [key]: typeof source[key] === "number" ? Number(source[key]) + 1 : "changed",
      })
    ).toBe(false);
  }
});
it("accepts complete inventories, filtered ordinals and sequential later pages", () => {
  expect(validateInstallPlanPage(page, plan, "items", 25, "", true)).toEqual(page);
  expect(
    validateInstallPlanPage(
      { ...page, items: [items[2]], totalCount: 1 },
      plan,
      "items",
      25,
      "postgres",
      true
    ).items
  ).toEqual([items[2]]);
  expect(
    validateInstallPlanPage({ ...page, items: [items[2]] }, plan, "items", 2, "", false).items
  ).toEqual([items[2]]);
  expect(
    validateInstallPlanPage(
      { ...page, items: items.slice(0, 2), nextCursor: "cursor" },
      plan,
      "items",
      2,
      "",
      true
    ).nextCursor
  ).toBe("cursor");
});
it.each([
  { ...page, planId: plan.operationId },
  { ...page, planDigest: "a".repeat(64) },
  { ...page, totalCount: 2 },
  { ...page, totalCount: 4 },
  { ...page, items: [] },
  { ...page, items: [items[0], items[0], items[2]] },
  { ...page, items: [items[0], items[2], items[1]] },
  { ...page, items: [items[0], items[2]] },
  { ...page, items: [{ ...items[0], proposedAction: "execute" }, ...items.slice(1)] },
  { ...page, items: [items[0], { ...items[1], originalObservation: "not-observed" }, items[2]] },
  { ...page, items: items.slice(0, 2) },
  { ...page, nextCursor: "" },
  { ...page, nextCursor: "cursor" },
])("refuses wrong source, truncation, gaps, duplicates and false actions %#", (value) => {
  expect(() => validateInstallPlanPage(value, plan, "items", 25, "", true)).toThrow();
});
it("validates workload precision and refuses false readiness", () => {
  const workloads = {
    planId: plan.id,
    planDigest: plan.plan.planDigest,
    view: "workloads",
    items: INSTALL_PLAN_PROPS.workloadTable.rows,
    totalCount: 2,
    nextCursor: null,
  };
  expect(validateInstallPlanPage(workloads, plan, "workloads", 25, "", true).items).toEqual(
    workloads.items
  );
  expect(() =>
    validateInstallPlanPage(
      { ...workloads, items: [{ ...workloads.items[0], readyReplicas: null }, workloads.items[1]] },
      plan,
      "workloads",
      25,
      "",
      true
    )
  ).toThrow();
});
it("preserves the exact private JSON text and checks the new plan identity", () => {
  const requestText = ` { "format": 1, "planId": "${plan.id}", "request": null, "maximumInputBytes": "9223372036854775807" }\n`;
  expect(installPlanFormSchema.parse({ planId: plan.id, requestText }).requestText).toBe(
    requestText
  );
  expect(installPlanFormSchema.safeParse({ planId: plan.operationId, requestText }).success).toBe(
    false
  );
  expect(
    installPlanFormSchema.safeParse({
      planId: plan.id,
      requestText: requestText.replace("null", "[]"),
    }).success
  ).toBe(false);
  expect(
    installPlanFormSchema.safeParse({
      planId: plan.id,
      requestText: requestText.replace("9223372036854775807", "0"),
    }).success
  ).toBe(false);
  expect(
    installPlanFormSchema.safeParse({
      planId: plan.id,
      requestText: requestText + " ".repeat(1048576),
    }).success
  ).toBe(false);
});
