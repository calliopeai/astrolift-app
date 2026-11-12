import { z } from "zod";
import type { NvkeInstallPlan } from "./operator-install-plan";
import type { NvkeInstallPlanCharts } from "./operator-install-plan-charts";
import type { NvkeInstallPlanTree } from "./operator-install-plan-tree";
import {
  installPlanWorkloadMetadataSchema,
  validateInstallPlanWorkloadSource,
  type NvkeInstallPlanWorkload,
} from "./operator-install-plan-workload";
import {
  installPlanImageBindingSource,
  validateInstallPlanImageBinding,
  type NvkeInstallPlanImageBinding,
} from "./operator-install-plan-image-binding";
import { validNvkeGuid } from "./recovery";

export type NvkeImmutableMode = "payload" | "execution";
export const IMMUTABLE_PAYLOAD_STATE =
  "requires-reviewed-operator-images-platform-credentials-import-use-and-linked-admission";
export const IMMUTABLE_EXECUTION_STATE =
  "requires-fresh-phase-platform-credential-import-use-and-linked-admission";
const guid = z.string().refine(validNvkeGuid);
const version = z.number().int().positive().safe();
const digest = z.string().regex(/^[0-9a-f]{64}$/);
const phase = z.string().regex(/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/);
const positiveDecimal = (maximum: string) =>
  z
    .string()
    .refine((value) => /^[1-9][0-9]{0,18}$/.test(value) && BigInt(value) <= BigInt(maximum));
const byteCount = z
  .string()
  .refine(
    (value) =>
      /^(0|[1-9][0-9]{0,18})$/.test(value) && BigInt(value) <= BigInt("9223372036854775807")
  );
const selectionSchema = z
  .record(phase, z.object({ artifactId: guid, artifactDigest: digest }).strict())
  .refine((value) => Object.keys(value).length > 0 && Object.keys(value).length <= 10000);

export function parsePayloadProofBindings(text: string) {
  if (text.length > 1048576) throw new Error("INVALID_PAYLOAD_BINDINGS");
  const parsed: unknown = JSON.parse(text);
  const tokens =
    text.match(/"(?:\\.|[^"\\])*"|[{}\[\],:]|true|false|null|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/g) ??
    [];
  let offset = 0;
  const walk = (depth: number): void => {
    if (depth > 8) throw new Error("INVALID_PAYLOAD_BINDINGS");
    const token = tokens[offset++];
    if (token === "{") {
      const keys = new Set<string>();
      while (tokens[offset] !== "}") {
        const key: unknown = JSON.parse(tokens[offset++]);
        if (typeof key !== "string" || keys.has(key) || tokens[offset++] !== ":")
          throw new Error("INVALID_PAYLOAD_BINDINGS");
        keys.add(key);
        walk(depth + 1);
        if (tokens[offset] !== ",") break;
        offset++;
      }
      if (tokens[offset++] !== "}") throw new Error("INVALID_PAYLOAD_BINDINGS");
    } else if (token === "[") {
      while (tokens[offset] !== "]") {
        walk(depth + 1);
        if (tokens[offset] !== ",") break;
        offset++;
      }
      if (tokens[offset++] !== "]") throw new Error("INVALID_PAYLOAD_BINDINGS");
    }
  };
  walk(0);
  if (offset !== tokens.length) throw new Error("INVALID_PAYLOAD_BINDINGS");
  return Object.fromEntries(
    Object.entries(selectionSchema.parse(parsed)).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
  );
}

const formShape = {
  artifactId: guid,
  item: z.string().max(128),
  payloadBindings: z.string().max(1048576),
  maximumBytes: z.string(),
  maximumResources: z.string(),
  timeoutSeconds: positiveDecimal("300"),
};
export type NvkeInstallPlanImmutableForm = z.infer<z.ZodObject<typeof formShape>>;
export function installPlanImmutableFormSchema(mode: NvkeImmutableMode) {
  return z
    .object(formShape)
    .strict()
    .superRefine((value, ctx) => {
      if (
        !positiveDecimal(mode === "payload" ? "67108864" : "9223372036854775807").safeParse(
          value.maximumBytes
        ).success
      )
        ctx.addIssue({ code: "custom", path: ["maximumBytes"], message: "INVALID_BUDGET" });
      if (mode === "payload") {
        if (!phase.safeParse(value.item).success)
          ctx.addIssue({ code: "custom", path: ["item"], message: "INVALID_PHASE" });
        if (!positiveDecimal("10000").safeParse(value.maximumResources).success)
          ctx.addIssue({ code: "custom", path: ["maximumResources"], message: "INVALID_BUDGET" });
        if (value.payloadBindings !== "")
          ctx.addIssue({
            code: "custom",
            path: ["payloadBindings"],
            message: "UNEXPECTED_SELECTION",
          });
      } else {
        try {
          parsePayloadProofBindings(value.payloadBindings);
        } catch {
          ctx.addIssue({ code: "custom", path: ["payloadBindings"], message: "INVALID_SELECTION" });
        }
        if (value.item !== "" || value.maximumResources !== "")
          ctx.addIssue({ code: "custom", path: ["item"], message: "UNEXPECTED_PHASE" });
      }
    });
}

const baseFields = z.object(installPlanWorkloadMetadataSchema.shape).omit({
  __typename: true,
  itemCount: true,
  resourceCount: true,
  imageUseCount: true,
  imageReferenceCount: true,
  issueCount: true,
  executionState: true,
}).shape;
const sourceFields = {
  ...baseFields,
  workloadId: guid,
  workloadVersion: version,
  bindingId: guid,
  bindingVersion: version,
  bindingDigest: digest,
  sourceObservedAt: installPlanWorkloadMetadataSchema.shape.observedAt,
};
export const installPlanImagePayloadSchema = z
  .object({
    ...sourceFields,
    __typename: z.literal("NvkeOperatorInstallPlanImagePayloadType").optional(),
    fileCount: z.number().int().min(1).max(10000),
    payloadDigest: digest,
    payloadBytes: byteCount,
    resourceCount: z.number().int().min(0).max(10000),
    imageUseCount: z.number().int().min(0).max(2147483647),
    rewriteCount: z.number().int().min(0).max(2147483647),
    executionState: z.literal(IMMUTABLE_PAYLOAD_STATE),
  })
  .strict()
  .refine(
    (row) =>
      row.rewriteCount <= row.imageUseCount &&
      (row.resourceCount === 0 || BigInt(row.payloadBytes) > BigInt(0)) &&
      (row.imageUseCount === 0 || row.resourceCount > 0)
  );
export const installPlanImmutableExecutionSchema = z
  .object({
    ...sourceFields,
    __typename: z.literal("NvkeOperatorInstallPlanImmutableExecutionType").optional(),
    fileCount: z.number().int().min(1).max(100000),
    executionDigest: digest,
    bytes: positiveDecimal("9223372036854775807"),
    phaseCount: z.number().int().min(1).max(10000),
    skippedPhaseCount: z.number().int().min(0).max(9999),
    importCount: z.number().int().min(0).max(10000),
    executionState: z.literal(IMMUTABLE_EXECUTION_STATE),
  })
  .strict();
export type NvkeInstallPlanImmutableArtifact =
  | z.infer<typeof installPlanImagePayloadSchema>
  | z.infer<typeof installPlanImmutableExecutionSchema>;

export function installPlanImmutableSource(
  parent: NvkeInstallPlan,
  publication: NvkeInstallPlanCharts,
  execution: NvkeInstallPlanTree,
  inventory: NvkeInstallPlanWorkload,
  images: NvkeInstallPlanImageBinding
) {
  const source = installPlanImageBindingSource(parent, publication, execution, inventory);
  const binding = validateInstallPlanImageBinding(
    images,
    source.plan,
    source.charts,
    source.tree,
    source.workload,
    images.id
  );
  return { ...source, binding };
}

export function installPlanImmutableRequest(
  values: unknown,
  mode: NvkeImmutableMode,
  parent: NvkeInstallPlan,
  publication: NvkeInstallPlanCharts,
  execution: NvkeInstallPlanTree,
  inventory: NvkeInstallPlanWorkload,
  images: NvkeInstallPlanImageBinding
) {
  const form = installPlanImmutableFormSchema(mode).parse(values);
  const { plan, charts, tree, workload, binding } = installPlanImmutableSource(
    parent,
    publication,
    execution,
    inventory,
    images
  );
  const ids = new Set([
    form.artifactId,
    plan.operationId,
    plan.id,
    charts.id,
    tree.id,
    workload.id,
    binding.id,
  ]);
  if (ids.size !== 7) throw new Error("INVALID_IMMUTABLE_IDENTITY");
  const base = {
    format: 1 as const,
    artifactId: form.artifactId,
    planId: plan.id,
    planDigest: plan.plan.planDigest,
    treeId: tree.id,
    treeDigest: tree.treeDigest,
    bindingId: binding.id,
    bindingDigest: binding.bindingDigest,
    maximumBytes: form.maximumBytes,
    timeoutSeconds: Number(form.timeoutSeconds),
  };
  let body;
  if (mode === "payload")
    body = { ...base, item: form.item, maximumResources: Number(form.maximumResources) };
  else {
    const payloadBindings = parsePayloadProofBindings(form.payloadBindings);
    if (Object.keys(payloadBindings).length !== plan.itemCount)
      throw new Error("INCOMPLETE_PHASE_SELECTION");
    for (const row of Object.values(payloadBindings)) {
      if (ids.has(row.artifactId)) throw new Error("INVALID_PAYLOAD_IDENTITY");
      ids.add(row.artifactId);
    }
    body = { ...base, payloadBindings };
  }
  if (new TextEncoder().encode(JSON.stringify(body)).length > 1048576)
    throw new Error("REQUEST_TOO_LARGE");
  return body;
}

export function validateInstallPlanImmutableArtifact(
  value: unknown,
  mode: NvkeImmutableMode,
  parent: NvkeInstallPlan,
  publication: NvkeInstallPlanCharts,
  execution: NvkeInstallPlanTree,
  inventory: NvkeInstallPlanWorkload,
  images: NvkeInstallPlanImageBinding,
  artifactId: string,
  limits?: NvkeInstallPlanImmutableForm
) {
  const { plan, charts, tree, workload, binding } = installPlanImmutableSource(
    parent,
    publication,
    execution,
    inventory,
    images
  );
  const row =
    mode === "payload"
      ? installPlanImagePayloadSchema.parse(value)
      : installPlanImmutableExecutionSchema.parse(value);
  validateInstallPlanWorkloadSource(row, plan, charts, tree, artifactId);
  if (
    new Set([
      row.id,
      row.operationId,
      row.planId,
      row.chartArtifactId,
      row.treeId,
      row.workloadId,
      row.bindingId,
    ]).size !== 7 ||
    row.workloadId !== workload.id ||
    row.workloadVersion !== workload.version ||
    row.workloadDigest !== workload.workloadDigest ||
    row.bindingId !== binding.id ||
    row.bindingVersion !== binding.version ||
    row.bindingDigest !== binding.bindingDigest ||
    Date.parse(row.sourceObservedAt) !== Date.parse(binding.observedAt) ||
    Date.parse(row.observedAt) < Date.parse(row.sourceObservedAt)
  )
    throw new Error("IMMUTABLE_SOURCE_CHANGED");
  if ("payloadDigest" in row) {
    if (row.resourceCount > workload.resourceCount || row.imageUseCount > binding.imageUseCount)
      throw new Error("PAYLOAD_SCOPE_EXCEEDED");
  } else if (
    row.phaseCount !== plan.itemCount - plan.plan.firstRemainingOrdinal ||
    row.skippedPhaseCount !== plan.plan.firstRemainingOrdinal ||
    row.importCount > binding.imageCount
  )
    throw new Error("EXECUTION_SCOPE_CHANGED");
  if (limits) {
    const form = installPlanImmutableFormSchema(mode).parse(limits);
    if (
      BigInt("payloadBytes" in row ? row.payloadBytes : row.bytes) > BigInt(form.maximumBytes) ||
      ("resourceCount" in row && row.resourceCount > Number(form.maximumResources))
    )
      throw new Error("IMMUTABLE_BUDGET_EXCEEDED");
  }
  return row;
}
