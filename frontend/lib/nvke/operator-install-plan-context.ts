import { z } from "zod";
import { installPlanSchema, type NvkeInstallPlan } from "./operator-install-plan";
import {
  validateInstallPlanCharts,
  type NvkeInstallPlanCharts,
} from "./operator-install-plan-charts";
import { validateInstallPlanTree, type NvkeInstallPlanTree } from "./operator-install-plan-tree";
import { validNvkeGuid } from "./recovery";

const guid = z.string().refine(validNvkeGuid);
const version = z.number().int().positive().safe();
const digest = z
  .string()
  .length(64)
  .regex(/^[0-9a-f]{64}$/);
const item = z
  .string()
  .regex(/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/)
  .refine((value) => value.trim() === value);
const decimal = (minimum: number, maximum: string) =>
  z
    .string()
    .refine(
      (value) =>
        /^[1-9][0-9]{0,18}$/.test(value) &&
        value.trim() === value &&
        BigInt(value) >= BigInt(minimum) &&
        BigInt(value) <= BigInt(maximum)
    );

export const installPlanContextSelectionSchema = z
  .object({
    item,
    ordinal: z.number().int().min(0).max(9999),
  })
  .strict();
export type NvkeInstallPlanContextSelection = z.infer<typeof installPlanContextSelectionSchema>;

export const installPlanContextFormSchema = z
  .object({
    artifactId: guid,
    item,
    ordinal: z
      .string()
      .regex(/^(0|[1-9][0-9]{0,3})$/)
      .refine((value) => value.trim() === value),
    revision: decimal(1, "9223372036854775807"),
    upgrade: z.boolean(),
    maximumBytes: decimal(1, "536870912"),
    maximumArtifactBytes: decimal(65536, "134217728"),
    maximumCalls: decimal(1, "2048"),
    maximumIterations: decimal(1, "64"),
    timeoutSeconds: decimal(1, "300"),
  })
  .strict()
  .refine((row) => row.upgrade || row.revision === "1");
export type NvkeInstallPlanContextForm = z.infer<typeof installPlanContextFormSchema>;

export const installPlanContextSchema = z
  .object({
    __typename: z.literal("NvkeOperatorInstallPlanContextType").optional(),
    id: guid,
    version,
    status: z.literal("prepared"),
    planId: guid,
    planVersion: version,
    planDigest: digest,
    chartArtifactId: guid,
    chartArtifactVersion: version,
    chartArtifactDigest: digest,
    treeId: guid,
    treeVersion: version,
    treeDigest: digest,
    operationId: guid,
    operationVersion: version,
    siteId: guid,
    siteVersion: version,
    authorityId: guid,
    authorityVersion: version,
    item,
    ordinal: z.number().int().min(0).max(9999),
    captureDigest: digest,
    frozenChartSha256: digest,
    contextDigest: digest,
    documentCount: z.number().int().min(0).max(10000),
    lookupCount: z.number().int().min(0).max(2048),
    observedAt: z
      .string()
      .max(40)
      .datetime({ offset: true })
      .refine((value) => value.trim() === value && Number.isFinite(Date.parse(value))),
    executionState: z.literal(
      "requires-authenticated-phase-verification-images-and-linked-admission"
    ),
    continuationAuthorized: z.literal(false),
  })
  .strict()
  .refine(
    (row) =>
      new Set([row.id, row.planId, row.chartArtifactId, row.treeId, row.operationId]).size === 5
  );
export type NvkeInstallPlanContext = z.infer<typeof installPlanContextSchema>;

export function installPlanContextRequest(
  values: unknown,
  parent: NvkeInstallPlan,
  publication: NvkeInstallPlanCharts,
  execution: NvkeInstallPlanTree
) {
  const form = installPlanContextFormSchema.parse(values),
    plan = installPlanSchema.parse(parent),
    charts = validateInstallPlanCharts(publication, plan, publication.id),
    tree = validateInstallPlanTree(execution, plan, charts, execution.id);
  if (new Set([form.artifactId, plan.id, charts.id, tree.id, plan.operationId]).size !== 5)
    throw new Error("INVALID_CONTEXT_IDENTITY");
  if (Number(form.ordinal) >= plan.itemCount) throw new Error("INVALID_CONTEXT_ITEM");
  return {
    format: 1 as const,
    artifactId: form.artifactId,
    planId: plan.id,
    planDigest: plan.plan.planDigest,
    treeId: tree.id,
    treeDigest: tree.treeDigest,
    item: form.item,
    revision: form.revision,
    upgrade: form.upgrade,
    maximumBytes: form.maximumBytes,
    maximumArtifactBytes: form.maximumArtifactBytes,
    maximumCalls: Number(form.maximumCalls),
    maximumIterations: Number(form.maximumIterations),
    timeoutSeconds: Number(form.timeoutSeconds),
  };
}

export function validateInstallPlanContext(
  value: unknown,
  parent: NvkeInstallPlan,
  publication: NvkeInstallPlanCharts,
  execution: NvkeInstallPlanTree,
  artifactId: string,
  selected: NvkeInstallPlanContextSelection
) {
  const row = installPlanContextSchema.parse(value),
    plan = installPlanSchema.parse(parent),
    charts = validateInstallPlanCharts(publication, plan, publication.id),
    tree = validateInstallPlanTree(execution, plan, charts, execution.id),
    selection = installPlanContextSelectionSchema.parse(selected);
  if (
    row.id !== artifactId ||
    row.planId !== plan.id ||
    row.planVersion !== plan.version ||
    row.planDigest !== plan.plan.planDigest ||
    row.chartArtifactId !== charts.id ||
    row.chartArtifactVersion !== charts.version ||
    row.chartArtifactDigest !== charts.artifactDigest ||
    row.treeId !== tree.id ||
    row.treeVersion !== tree.version ||
    row.treeDigest !== tree.treeDigest ||
    row.operationId !== plan.operationId ||
    row.operationVersion !== plan.operationVersion ||
    row.siteId !== plan.siteId ||
    row.siteVersion !== plan.siteVersion ||
    row.authorityId !== plan.authorityId ||
    row.authorityVersion !== plan.authorityVersion ||
    row.item !== selection.item ||
    row.ordinal !== selection.ordinal ||
    row.ordinal >= plan.itemCount
  )
    throw new Error("CONTEXT_SOURCE_CHANGED");
  return row;
}
