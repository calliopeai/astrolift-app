import { z } from "zod";
import { installPlanSchema, type NvkeInstallPlan } from "./operator-install-plan";
import {
  validateInstallPlanCharts,
  type NvkeInstallPlanCharts,
} from "./operator-install-plan-charts";
import { validNvkeGuid } from "./recovery";

const guid = z.string().refine(validNvkeGuid);
const version = z.number().int().positive().safe();
const digest = z.string().regex(/^[0-9a-f]{64}$/);
const budget = z
  .string()
  .regex(/^[1-9][0-9]{0,18}$/)
  .refine(
    (value) => /^[1-9][0-9]{0,18}$/.test(value) && BigInt(value) <= BigInt("9223372036854775807")
  );

export const installPlanTreeSchema = z
  .object({
    __typename: z.literal("NvkeOperatorInstallPlanTreeType").optional(),
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
    chartArtifactId: guid,
    chartArtifactVersion: version,
    chartArtifactDigest: digest,
    treeDigest: digest,
    fileCount: z.number().int().min(1).max(100000),
    bytes: budget,
    executionState: z.literal("requires-native-rendering-images-and-live-verification"),
    continuationAuthorized: z.literal(false),
  })
  .strict()
  .refine((row) => new Set([row.id, row.planId, row.operationId, row.chartArtifactId]).size === 4);
export type NvkeInstallPlanTree = z.infer<typeof installPlanTreeSchema>;

export const installPlanTreeFormSchema = z
  .object({ artifactId: guid, maximumBytes: budget })
  .strict();
export type NvkeInstallPlanTreeForm = z.infer<typeof installPlanTreeFormSchema>;

export function installPlanTreeRequest(
  values: NvkeInstallPlanTreeForm,
  parent: NvkeInstallPlan,
  publication: NvkeInstallPlanCharts
) {
  const form = installPlanTreeFormSchema.parse(values),
    plan = installPlanSchema.parse(parent),
    charts = validateInstallPlanCharts(publication, plan, publication.id);
  if (new Set([form.artifactId, plan.id, plan.operationId, charts.id]).size !== 4)
    throw new Error("INVALID_TREE_IDENTITY");
  if (BigInt(form.maximumBytes) < BigInt(charts.bytes)) throw new Error("TREE_BUDGET_EXCEEDED");
  return {
    format: 1 as const,
    artifactId: form.artifactId,
    planId: plan.id,
    planDigest: plan.plan.planDigest,
    chartArtifactId: charts.id,
    chartArtifactDigest: charts.artifactDigest,
    maximumBytes: form.maximumBytes,
  };
}

export function validateInstallPlanTree(
  value: unknown,
  parent: NvkeInstallPlan,
  publication: NvkeInstallPlanCharts,
  artifactId: string,
  limits?: Pick<NvkeInstallPlanTreeForm, "maximumBytes">
) {
  const row = installPlanTreeSchema.parse(value),
    plan = installPlanSchema.parse(parent),
    charts = validateInstallPlanCharts(publication, plan, publication.id);
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
    row.chartArtifactId !== charts.id ||
    row.chartArtifactVersion !== charts.version ||
    row.chartArtifactDigest !== charts.artifactDigest
  )
    throw new Error("TREE_SOURCE_CHANGED");
  if (row.fileCount < charts.chartCount || BigInt(row.bytes) < BigInt(charts.bytes))
    throw new Error("TREE_CONTENT_INCOMPLETE");
  if (limits && BigInt(row.bytes) > BigInt(budget.parse(limits.maximumBytes)))
    throw new Error("TREE_BUDGET_EXCEEDED");
  return row;
}
