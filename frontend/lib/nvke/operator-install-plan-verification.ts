import { z } from "zod";
import { installPlanSchema, type NvkeInstallPlan } from "./operator-install-plan";
import {
  validateInstallPlanCharts,
  type NvkeInstallPlanCharts,
} from "./operator-install-plan-charts";
import { validateInstallPlanTree, type NvkeInstallPlanTree } from "./operator-install-plan-tree";
import {
  validateInstallPlanContext,
  type NvkeInstallPlanContext,
} from "./operator-install-plan-context";
import { validNvkeGuid } from "./recovery";

const guid = z.string().refine(validNvkeGuid);
const version = z.number().int().positive().safe();
const digest = z
  .string()
  .length(64)
  .regex(/^[0-9a-f]{64}$/);
const decimal = (maximum: string) =>
  z
    .string()
    .refine(
      (value) =>
        /^[1-9][0-9]{0,18}$/.test(value) &&
        value.trim() === value &&
        BigInt(value) <= BigInt(maximum)
    );

export const installPlanVerificationFormSchema = z
  .object({
    artifactId: guid,
    maximumBytes: decimal("536870912"),
    maximumCaptureAgeSeconds: decimal("3600"),
    maximumCalls: decimal("2048"),
    timeoutSeconds: decimal("300"),
  })
  .strict();
export type NvkeInstallPlanVerificationForm = z.infer<typeof installPlanVerificationFormSchema>;

export const installPlanVerificationSchema = z
  .object({
    __typename: z.literal("NvkeOperatorInstallPlanVerificationType").optional(),
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
    contextId: guid,
    contextVersion: version,
    contextDigest: digest,
    operationId: guid,
    operationVersion: version,
    siteId: guid,
    siteVersion: version,
    authorityId: guid,
    authorityVersion: version,
    item: z
      .string()
      .regex(/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/)
      .refine((value) => value.trim() === value),
    ordinal: z.number().int().min(0).max(9999),
    captureDigest: digest,
    frozenChartSha256: digest,
    artifactDigest: digest,
    verificationDigest: digest,
    requests: z.number().int().min(1).max(2048),
    bytes: decimal("536870912"),
    observedAt: z
      .string()
      .max(40)
      .datetime({ offset: true })
      .refine((value) => value.trim() === value && Number.isFinite(Date.parse(value))),
    executionState: z.literal(
      "requires-phase-use-resource-credential-schema-images-and-linked-admission"
    ),
    continuationAuthorized: z.literal(false),
  })
  .strict()
  .refine(
    (row) =>
      new Set([row.id, row.contextId, row.treeId, row.chartArtifactId, row.planId, row.operationId])
        .size === 6
  );
export type NvkeInstallPlanVerification = z.infer<typeof installPlanVerificationSchema>;

function source(
  parent: NvkeInstallPlan,
  publication: NvkeInstallPlanCharts,
  execution: NvkeInstallPlanTree,
  captured: NvkeInstallPlanContext
) {
  const plan = installPlanSchema.parse(parent),
    charts = validateInstallPlanCharts(publication, plan, publication.id),
    tree = validateInstallPlanTree(execution, plan, charts, execution.id),
    context = validateInstallPlanContext(captured, plan, charts, tree, captured.id, {
      item: captured.item,
      ordinal: captured.ordinal,
    });
  return { plan, charts, tree, context };
}

export function installPlanVerificationRequest(
  values: unknown,
  parent: NvkeInstallPlan,
  publication: NvkeInstallPlanCharts,
  execution: NvkeInstallPlanTree,
  captured: NvkeInstallPlanContext
) {
  const form = installPlanVerificationFormSchema.parse(values),
    { plan, charts, tree, context } = source(parent, publication, execution, captured);
  if (
    new Set([form.artifactId, context.id, tree.id, charts.id, plan.id, plan.operationId]).size !== 6
  )
    throw new Error("INVALID_VERIFICATION_IDENTITY");
  return {
    format: 1 as const,
    artifactId: form.artifactId,
    contextId: context.id,
    contextDigest: context.contextDigest,
    planId: plan.id,
    planDigest: plan.plan.planDigest,
    treeId: tree.id,
    treeDigest: tree.treeDigest,
    item: context.item,
    maximumBytes: form.maximumBytes,
    maximumCaptureAgeSeconds: Number(form.maximumCaptureAgeSeconds),
    maximumCalls: Number(form.maximumCalls),
    timeoutSeconds: Number(form.timeoutSeconds),
  };
}

export function validateInstallPlanVerification(
  value: unknown,
  parent: NvkeInstallPlan,
  publication: NvkeInstallPlanCharts,
  execution: NvkeInstallPlanTree,
  captured: NvkeInstallPlanContext,
  artifactId: string
) {
  const row = installPlanVerificationSchema.parse(value),
    { plan, charts, tree, context } = source(parent, publication, execution, captured);
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
    row.contextId !== context.id ||
    row.contextVersion !== context.version ||
    row.contextDigest !== context.contextDigest ||
    row.operationId !== plan.operationId ||
    row.operationVersion !== plan.operationVersion ||
    row.siteId !== plan.siteId ||
    row.siteVersion !== plan.siteVersion ||
    row.authorityId !== plan.authorityId ||
    row.authorityVersion !== plan.authorityVersion ||
    row.item !== context.item ||
    row.ordinal !== context.ordinal ||
    row.captureDigest !== context.captureDigest ||
    row.frozenChartSha256 !== context.frozenChartSha256
  )
    throw new Error("VERIFICATION_SOURCE_CHANGED");
  return row;
}
