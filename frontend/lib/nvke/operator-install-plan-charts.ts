import { z } from "zod";
import { installPlanSchema, type NvkeInstallPlan } from "./operator-install-plan";
import { validNvkeGuid } from "./recovery";

const guid = z.string().refine(validNvkeGuid);
const version = z.number().int().positive().safe();
const digest = z.string().regex(/^[0-9a-f]{64}$/);
const decimal = z
  .string()
  .regex(/^(0|[1-9][0-9]{0,18})$/)
  .refine(
    (value) =>
      /^(0|[1-9][0-9]{0,18})$/.test(value) && BigInt(value) <= BigInt("9223372036854775807")
  );
const budget = decimal.refine((value) => value !== "0");

export const installPlanChartsSchema = z
  .object({
    __typename: z.literal("NvkeOperatorInstallPlanChartsType").optional(),
    id: guid,
    version,
    status: z.literal("prepared"),
    planId: guid,
    planVersion: version,
    operationId: guid,
    operationVersion: version,
    siteId: guid,
    siteVersion: version,
    authorityId: guid,
    authorityVersion: version,
    planDigest: digest,
    artifactDigest: digest,
    chartCount: z.number().int().min(0).max(10000),
    bytes: decimal,
    expandedBytes: decimal,
    executionState: z.literal("requires-rendering-images-and-live-verification"),
    continuationAuthorized: z.literal(false),
  })
  .strict()
  .refine(
    (row) =>
      (row.chartCount === 0) === (row.bytes === "0") &&
      (row.chartCount === 0) === (row.expandedBytes === "0") &&
      row.id !== row.planId &&
      row.id !== row.operationId
  );
export type NvkeInstallPlanCharts = z.infer<typeof installPlanChartsSchema>;

export const installPlanChartsFormSchema = z
  .object({
    artifactId: guid,
    maximumBytes: budget,
    maximumExpandedBytes: budget,
    timeoutSeconds: z
      .string()
      .regex(/^[1-9][0-9]{0,3}$/)
      .refine((value) => Number(value) <= 3600),
    allowPlainHttp: z.boolean(),
  })
  .strict();
export type NvkeInstallPlanChartsForm = z.infer<typeof installPlanChartsFormSchema>;

export function installPlanChartsRequest(
  values: NvkeInstallPlanChartsForm,
  parent: NvkeInstallPlan
) {
  const form = installPlanChartsFormSchema.parse(values),
    plan = installPlanSchema.parse(parent);
  if (form.artifactId === plan.id || form.artifactId === plan.operationId)
    throw new Error("INVALID_CHART_IDENTITY");
  return {
    format: 1 as const,
    artifactId: form.artifactId,
    planId: plan.id,
    planDigest: plan.plan.planDigest,
    maximumBytes: form.maximumBytes,
    maximumExpandedBytes: form.maximumExpandedBytes,
    timeoutSeconds: Number(form.timeoutSeconds),
    allowPlainHttp: form.allowPlainHttp,
  };
}

export function validateInstallPlanCharts(
  value: unknown,
  parent: NvkeInstallPlan,
  artifactId: string,
  limits?: Pick<NvkeInstallPlanChartsForm, "maximumBytes" | "maximumExpandedBytes">
) {
  const row = installPlanChartsSchema.parse(value),
    plan = installPlanSchema.parse(parent);
  if (
    row.id !== artifactId ||
    row.planId !== plan.id ||
    row.planVersion !== plan.version ||
    row.planDigest !== plan.plan.planDigest ||
    row.operationId !== plan.operationId ||
    row.operationVersion !== plan.operationVersion ||
    row.siteId !== plan.siteId ||
    row.siteVersion !== plan.siteVersion ||
    row.authorityId !== plan.authorityId ||
    row.authorityVersion !== plan.authorityVersion ||
    row.chartCount > plan.itemCount
  )
    throw new Error("CHART_SOURCE_CHANGED");
  if (
    limits &&
    (BigInt(row.bytes) > BigInt(budget.parse(limits.maximumBytes)) ||
      BigInt(row.expandedBytes) > BigInt(budget.parse(limits.maximumExpandedBytes)))
  )
    throw new Error("CHART_BUDGET_EXCEEDED");
  return row;
}
