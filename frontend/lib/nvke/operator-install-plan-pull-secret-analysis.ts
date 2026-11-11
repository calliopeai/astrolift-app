import { z } from "zod";
import type { NvkeInstallPlan } from "./operator-install-plan";
import type { NvkeInstallPlanCharts } from "./operator-install-plan-charts";
import type { NvkeInstallPlanTree } from "./operator-install-plan-tree";
import type { NvkeInstallPlanImageBinding } from "./operator-install-plan-image-binding";
import {
  installPlanWorkloadMetadataSchema,
  validateInstallPlanWorkloadSource,
  type NvkeInstallPlanWorkload,
} from "./operator-install-plan-workload";
import {
  parsePullAuthorityScope,
  validateInstallPlanPullAuthority,
  type NvkeInstallPlanPullAuthority,
} from "./operator-install-plan-pull-authority";
import { installPlanImmutableSource } from "./operator-install-plan-immutable";
import { validNvkeGuid } from "./recovery";

export const PULL_SECRET_ANALYSIS_STATE =
  "requires-pod-ownership-kubelet-configuration-plugin-registry-correspondence-and-linked-admission";
const guid = z.string().refine(validNvkeGuid);
const digest = z
  .string()
  .length(64)
  .regex(/^[0-9a-f]{64}$/);
const version = z.number().int().positive().safe();
const decimal = (maximum: string) =>
  z
    .string()
    .refine(
      (value) =>
        value.trim() === value &&
        /^[1-9][0-9]{0,18}$/.test(value) &&
        BigInt(value) <= BigInt(maximum)
    );
const selectedImages = z
  .array(
    z
      .object({
        podKey: z.string().refine((value) => {
          try {
            parsePullAuthorityScope(JSON.stringify({ [value]: "selection-placeholder" }));
            return true;
          } catch {
            return false;
          }
        }),
        imagePointer: z
          .string()
          .refine((value) => value.trim() === value)
          .regex(
            /^\/spec\/(containers|initContainers|ephemeralContainers)\/(0|[1-9][0-9]{0,5})\/image$/
          ),
        ensureSecretPulledImages: z.boolean(),
      })
      .strict()
  )
  .min(1)
  .max(1024)
  .refine(
    (rows) =>
      new Set(rows.map((row) => JSON.stringify([row.podKey, row.imagePointer]))).size ===
      rows.length
  );

export function parsePullSecretAnalysisSelections(text: string) {
  if (new TextEncoder().encode(text).length > 1048576) throw new Error("INVALID_IMAGE_USES");
  const parsed: unknown = JSON.parse(text);
  const tokens =
    text.match(/"(?:\\.|[^"\\])*"|[{}\[\],:]|true|false|null|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/g) ??
    [];
  let offset = 0;
  const walk = (depth: number): void => {
    if (depth > 8) throw new Error("INVALID_IMAGE_USES");
    const token = tokens[offset++];
    if (token === "{") {
      const keys = new Set<string>();
      while (tokens[offset] !== "}") {
        const key: unknown = JSON.parse(tokens[offset++]);
        if (typeof key !== "string" || keys.has(key) || tokens[offset++] !== ":")
          throw new Error("INVALID_IMAGE_USES");
        keys.add(key);
        walk(depth + 1);
        if (tokens[offset] !== ",") break;
        offset++;
      }
      if (tokens[offset++] !== "}") throw new Error("INVALID_IMAGE_USES");
    } else if (token === "[") {
      while (tokens[offset] !== "]") {
        walk(depth + 1);
        if (tokens[offset] !== ",") break;
        offset++;
      }
      if (tokens[offset++] !== "]") throw new Error("INVALID_IMAGE_USES");
    }
  };
  walk(0);
  if (offset !== tokens.length) throw new Error("INVALID_IMAGE_USES");
  return selectedImages.parse(parsed);
}

export const installPlanPullSecretAnalysisFormSchema = z
  .object({
    artifactId: guid,
    imageSelections: z.string().max(1048576),
    maximumBytes: decimal("67108864"),
    maximumHelperBytes: decimal("67108864"),
    timeoutSeconds: decimal("300"),
  })
  .strict()
  .superRefine((value, ctx) => {
    try {
      parsePullSecretAnalysisSelections(value.imageSelections);
      if (BigInt(value.maximumHelperBytes) > BigInt(value.maximumBytes))
        ctx.addIssue({
          code: "custom",
          path: ["maximumHelperBytes"],
          message: "OUTPUT_BUDGET_EXCEEDS_TOTAL",
        });
    } catch {
      ctx.addIssue({ code: "custom", path: ["imageSelections"], message: "INVALID_IMAGE_USES" });
    }
  });
export type NvkeInstallPlanPullSecretAnalysisForm = z.infer<
  typeof installPlanPullSecretAnalysisFormSchema
>;
const fields = z.object(installPlanWorkloadMetadataSchema.shape).omit({
  __typename: true,
  itemCount: true,
  resourceCount: true,
  imageUseCount: true,
  imageReferenceCount: true,
  issueCount: true,
  executionState: true,
  observedAt: true,
}).shape;
export const installPlanPullSecretAnalysisSchema = z
  .object({
    ...fields,
    __typename: z.literal("NvkeOperatorInstallPlanPullSecretAnalysisType").optional(),
    workloadId: guid,
    workloadVersion: version,
    bindingId: guid,
    bindingVersion: version,
    bindingDigest: digest,
    pullAuthorityId: guid,
    pullAuthorityVersion: version,
    pullAuthorityDigest: digest,
    authorityDigest: digest,
    analyzedAt: installPlanWorkloadMetadataSchema.shape.observedAt,
    sourceObservedAt: installPlanWorkloadMetadataSchema.shape.observedAt,
    imageUseCount: z.number().int().min(1).max(1024),
    candidateCount: z.number().int().min(0).max(102400000),
    missingReferenceCount: z.number().int().min(0).max(10240000),
    executionState: z.literal(PULL_SECRET_ANALYSIS_STATE),
  })
  .strict()
  .refine(
    (row) =>
      row.candidateCount <= row.imageUseCount * 100000 &&
      row.missingReferenceCount <= row.imageUseCount * 10000
  );
export type NvkeInstallPlanPullSecretAnalysis = z.infer<typeof installPlanPullSecretAnalysisSchema>;

export function installPlanPullSecretAnalysisSource(
  parent: NvkeInstallPlan,
  publication: NvkeInstallPlanCharts,
  execution: NvkeInstallPlanTree,
  inventory: NvkeInstallPlanWorkload,
  images: NvkeInstallPlanImageBinding,
  observation: NvkeInstallPlanPullAuthority
) {
  const source = installPlanImmutableSource(parent, publication, execution, inventory, images);
  const pullAuthority = validateInstallPlanPullAuthority(
    observation,
    source.plan,
    source.charts,
    source.tree,
    source.workload,
    source.binding,
    observation.id
  );
  return { ...source, pullAuthority };
}

export function installPlanPullSecretAnalysisRequest(
  values: unknown,
  parent: NvkeInstallPlan,
  publication: NvkeInstallPlanCharts,
  execution: NvkeInstallPlanTree,
  inventory: NvkeInstallPlanWorkload,
  images: NvkeInstallPlanImageBinding,
  observation: NvkeInstallPlanPullAuthority
) {
  const form = installPlanPullSecretAnalysisFormSchema.parse(values);
  const { plan, charts, tree, workload, binding, pullAuthority } =
    installPlanPullSecretAnalysisSource(
      parent,
      publication,
      execution,
      inventory,
      images,
      observation
    );
  if (
    new Set([
      form.artifactId,
      plan.operationId,
      plan.id,
      charts.id,
      tree.id,
      workload.id,
      binding.id,
      pullAuthority.id,
    ]).size !== 8
  )
    throw new Error("INVALID_PULL_SECRET_ANALYSIS_IDENTITY");
  const body = {
    format: 1 as const,
    artifactId: form.artifactId,
    planId: plan.id,
    planDigest: plan.plan.planDigest,
    treeId: tree.id,
    treeDigest: tree.treeDigest,
    bindingId: binding.id,
    bindingDigest: binding.bindingDigest,
    pullAuthorityId: pullAuthority.id,
    pullAuthorityDigest: pullAuthority.authorityDigest,
    imageSelections: parsePullSecretAnalysisSelections(form.imageSelections),
    maximumBytes: form.maximumBytes,
    maximumHelperBytes: form.maximumHelperBytes,
    timeoutSeconds: Number(form.timeoutSeconds),
  };
  if (new TextEncoder().encode(JSON.stringify(body)).length > 1048576)
    throw new Error("REQUEST_TOO_LARGE");
  return body;
}

export function validateInstallPlanPullSecretAnalysis(
  value: unknown,
  parent: NvkeInstallPlan,
  publication: NvkeInstallPlanCharts,
  execution: NvkeInstallPlanTree,
  inventory: NvkeInstallPlanWorkload,
  images: NvkeInstallPlanImageBinding,
  observation: NvkeInstallPlanPullAuthority,
  artifactId: string,
  limits?: NvkeInstallPlanPullSecretAnalysisForm
) {
  const { plan, charts, tree, workload, binding, pullAuthority } =
    installPlanPullSecretAnalysisSource(
      parent,
      publication,
      execution,
      inventory,
      images,
      observation
    );
  const row = installPlanPullSecretAnalysisSchema.parse(value);
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
      row.pullAuthorityId,
    ]).size !== 8 ||
    row.workloadId !== workload.id ||
    row.workloadVersion !== workload.version ||
    row.workloadDigest !== workload.workloadDigest ||
    row.bindingId !== binding.id ||
    row.bindingVersion !== binding.version ||
    row.bindingDigest !== binding.bindingDigest ||
    row.pullAuthorityId !== pullAuthority.id ||
    row.pullAuthorityVersion !== pullAuthority.version ||
    row.pullAuthorityDigest !== pullAuthority.authorityDigest ||
    Date.parse(row.sourceObservedAt) < Date.parse(binding.observedAt) ||
    Date.parse(row.sourceObservedAt) > Date.parse(pullAuthority.observedAt) ||
    Date.parse(row.analyzedAt) < Date.parse(pullAuthority.observedAt)
  )
    throw new Error("PULL_SECRET_ANALYSIS_SOURCE_CHANGED");
  if (
    limits &&
    row.imageUseCount !==
      parsePullSecretAnalysisSelections(
        installPlanPullSecretAnalysisFormSchema.parse(limits).imageSelections
      ).length
  )
    throw new Error("PULL_SECRET_ANALYSIS_SCOPE_EXCEEDED");
  return row;
}
