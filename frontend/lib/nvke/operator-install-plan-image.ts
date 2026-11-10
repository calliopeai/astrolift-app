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
const decimal = (maximum: string) =>
  z
    .string()
    .refine(
      (value) =>
        value.trim() === value &&
        /^[1-9][0-9]{0,18}$/.test(value) &&
        BigInt(value) <= BigInt(maximum)
    );
const bytes = decimal("9223372036854775807");

export const installPlanImageFormSchema = z
  .object({
    artifactId: guid,
    reference: z
      .string()
      .min(1)
      .max(4096)
      .regex(/^[\x21-\x7e]+$/)
      .refine((value) => value.trim() === value && !value.includes("://") && !/[?#\\]/.test(value)),
    policy: z
      .string()
      .regex(/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/)
      .refine((value) => value.trim() === value),
    maximumBytes: bytes,
    maximumCalls: decimal("4096"),
    timeoutSeconds: decimal("300"),
  })
  .strict();
export type NvkeInstallPlanImageForm = z.infer<typeof installPlanImageFormSchema>;

export const installPlanImageSchema = z
  .object({
    __typename: z.literal("NvkeOperatorInstallPlanImageType").optional(),
    id: guid,
    version,
    status: z.literal("prepared"),
    sourceKind: z.literal("registry").optional(),
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
    artifactDigest: digest,
    sourceDigest: digest,
    captureDigest: digest,
    rootDigest: z
      .string()
      .regex(/^(sha256:[0-9a-f]{64}|sha512:[0-9a-f]{128})$/)
      .refine((value) => value.trim() === value),
    blobCount: z.number().int().min(1).max(100000),
    bytes,
    observedAt: z
      .string()
      .max(40)
      .datetime({ offset: true })
      .refine((value) => value.trim() === value && Number.isFinite(Date.parse(value))),
    executionState: z.literal(
      "requires-workload-platform-credential-phase-use-and-linked-admission"
    ),
    continuationAuthorized: z.literal(false),
  })
  .strict()
  .refine(
    (row) =>
      new Set([row.id, row.treeId, row.chartArtifactId, row.planId, row.operationId]).size === 5
  );
export type NvkeInstallPlanImage = z.infer<typeof installPlanImageSchema>;

function source(
  parent: NvkeInstallPlan,
  publication: NvkeInstallPlanCharts,
  execution: NvkeInstallPlanTree
) {
  const plan = installPlanSchema.parse(parent);
  const charts = validateInstallPlanCharts(publication, plan, publication.id);
  const tree = validateInstallPlanTree(execution, plan, charts, execution.id);
  return { plan, charts, tree };
}

export function installPlanImageRequest(
  values: unknown,
  parent: NvkeInstallPlan,
  publication: NvkeInstallPlanCharts,
  execution: NvkeInstallPlanTree
) {
  const form = installPlanImageFormSchema.parse(values);
  const { plan, charts, tree } = source(parent, publication, execution);
  if (new Set([form.artifactId, tree.id, charts.id, plan.id, plan.operationId]).size !== 5)
    throw new Error("INVALID_IMAGE_IDENTITY");
  return {
    format: 1 as const,
    artifactId: form.artifactId,
    planId: plan.id,
    planDigest: plan.plan.planDigest,
    treeId: tree.id,
    treeDigest: tree.treeDigest,
    reference: form.reference,
    policy: form.policy,
    maximumBytes: form.maximumBytes,
    maximumCalls: Number(form.maximumCalls),
    timeoutSeconds: Number(form.timeoutSeconds),
  };
}

export function validateInstallPlanImage(
  value: unknown,
  parent: NvkeInstallPlan,
  publication: NvkeInstallPlanCharts,
  execution: NvkeInstallPlanTree,
  artifactId: string,
  limits?: Pick<NvkeInstallPlanImageForm, "maximumBytes">
) {
  const row = installPlanImageSchema.parse(value);
  const { plan, charts, tree } = source(parent, publication, execution);
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
    row.authorityVersion !== plan.authorityVersion
  )
    throw new Error("IMAGE_SOURCE_CHANGED");
  if (limits && BigInt(row.bytes) > BigInt(bytes.parse(limits.maximumBytes)))
    throw new Error("IMAGE_BUDGET_EXCEEDED");
  return row;
}
