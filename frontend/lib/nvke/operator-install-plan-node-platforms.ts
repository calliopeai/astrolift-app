import { z } from "zod";
import type { NvkeInstallPlan } from "./operator-install-plan";
import type { NvkeInstallPlanCharts } from "./operator-install-plan-charts";
import type { NvkeInstallPlanTree } from "./operator-install-plan-tree";
import {
  installPlanWorkloadMetadataSchema,
  validateInstallPlanWorkloadSource,
  type NvkeInstallPlanWorkload,
} from "./operator-install-plan-workload";
import type { NvkeInstallPlanImageBinding } from "./operator-install-plan-image-binding";
import { installPlanImmutableSource } from "./operator-install-plan-immutable";
import { validNvkeGuid } from "./recovery";

export const NODE_PLATFORMS_STATE =
  "requires-attested-cpu-importer-scheduling-credentials-availability-phase-use-and-linked-admission";
export const NODE_RUNTIME_PROFILES = [
  "containerd/v2.3.0",
  "containerd/v2.3.7",
  "containerd/v2.2.3",
  "containerd/v2.1.7",
  "containerd/v1.7.31",
] as const;
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
        /^[1-9][0-9]{0,18}$/.test(value) &&
        value.trim() === value &&
        BigInt(value) <= BigInt(maximum)
    );
const nodeName = z
  .string()
  .max(253)
  .refine(
    (value) =>
      value.trim() === value &&
      /^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?(?:\.[a-z0-9](?:[-a-z0-9]*[a-z0-9])?)*$/.test(value)
  );
const uid = z
  .string()
  .refine((value) => value.trim() === value && /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(value));
const scopeSchema = z
  .record(
    nodeName,
    z
      .object({
        uid,
        runtimeProfile: z.enum(NODE_RUNTIME_PROFILES),
        platform: z
          .object({
            os: z.string().min(1),
            architecture: z.string().min(1),
            variant: z.string().optional(),
            "os.version": z.string().optional(),
            "os.features": z.array(z.string()).optional(),
          })
          .strict(),
      })
      .strict()
  )
  .refine((value) => {
    const rows = Object.values(value);
    return (
      rows.length > 0 &&
      rows.length <= 10000 &&
      new Set(rows.map((row) => row.uid)).size === rows.length
    );
  });

export function parseNodePlatformScope(text: string) {
  if (new TextEncoder().encode(text).length > 1048576) throw new Error("INVALID_NODE_SCOPE");
  const parsed: unknown = JSON.parse(text);
  const tokens =
    text.match(/"(?:\\.|[^"\\])*"|[{}\[\],:]|true|false|null|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/g) ??
    [];
  let offset = 0;
  const walk = (depth: number): void => {
    if (depth > 8) throw new Error("INVALID_NODE_SCOPE");
    const token = tokens[offset++];
    if (token === "{") {
      const keys = new Set<string>();
      while (tokens[offset] !== "}") {
        const key: unknown = JSON.parse(tokens[offset++]);
        if (typeof key !== "string" || keys.has(key) || tokens[offset++] !== ":")
          throw new Error("INVALID_NODE_SCOPE");
        keys.add(key);
        walk(depth + 1);
        if (tokens[offset] !== ",") break;
        offset++;
      }
      if (tokens[offset++] !== "}") throw new Error("INVALID_NODE_SCOPE");
    } else if (token === "[") {
      while (tokens[offset] !== "]") {
        walk(depth + 1);
        if (tokens[offset] !== ",") break;
        offset++;
      }
      if (tokens[offset++] !== "]") throw new Error("INVALID_NODE_SCOPE");
    }
  };
  walk(0);
  if (offset !== tokens.length) throw new Error("INVALID_NODE_SCOPE");
  return Object.fromEntries(
    Object.entries(scopeSchema.parse(parsed)).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
  );
}

export const installPlanNodePlatformsFormSchema = z
  .object({
    artifactId: guid,
    nodePlatforms: z.string().max(1048576),
    maximumImageBytes: decimal("9223372036854775807"),
    maximumMetadataBytes: decimal("67108864"),
    maximumBytes: decimal("16777216"),
    maximumObservationBytes: decimal("67108864"),
    maximumHelperBytes: decimal("9223372036854775807"),
    maximumCalls: decimal("65536"),
    maximumPairs: decimal("100000"),
    timeoutSeconds: decimal("300"),
  })
  .strict()
  .superRefine((value, ctx) => {
    try {
      const scope = parseNodePlatformScope(value.nodePlatforms);
      if (Number(value.maximumCalls) < 4 * Object.keys(scope).length + 4)
        ctx.addIssue({
          code: "custom",
          path: ["maximumCalls"],
          message: "INSUFFICIENT_READ_BUDGET",
        });
    } catch {
      ctx.addIssue({ code: "custom", path: ["nodePlatforms"], message: "INVALID_NODE_SCOPE" });
    }
  });
export type NvkeInstallPlanNodePlatformsForm = z.infer<typeof installPlanNodePlatformsFormSchema>;

const baseFields = z.object(installPlanWorkloadMetadataSchema.shape).omit({
  __typename: true,
  itemCount: true,
  resourceCount: true,
  imageUseCount: true,
  imageReferenceCount: true,
  issueCount: true,
  executionState: true,
}).shape;
export const installPlanNodePlatformsSchema = z
  .object({
    ...baseFields,
    __typename: z.literal("NvkeOperatorInstallPlanNodePlatformsType").optional(),
    workloadId: guid,
    workloadVersion: version,
    bindingId: guid,
    bindingVersion: version,
    bindingDigest: digest,
    platformDigest: digest,
    nodeCount: z.number().int().min(1).max(10000),
    contentCount: z.number().int().min(0).max(100000),
    imageReferenceCount: z.number().int().min(0).max(2147483647),
    pairCount: z.number().int().min(0).max(100000),
    issueCount: z.number().int().min(0).max(2147483647),
    executionState: z.literal(NODE_PLATFORMS_STATE),
  })
  .strict()
  .refine(
    (row) =>
      row.contentCount <= row.imageReferenceCount &&
      row.pairCount === row.nodeCount * row.contentCount
  );
export type NvkeInstallPlanNodePlatforms = z.infer<typeof installPlanNodePlatformsSchema>;

export function installPlanNodePlatformsRequest(
  values: unknown,
  parent: NvkeInstallPlan,
  publication: NvkeInstallPlanCharts,
  execution: NvkeInstallPlanTree,
  inventory: NvkeInstallPlanWorkload,
  images: NvkeInstallPlanImageBinding
) {
  const form = installPlanNodePlatformsFormSchema.parse(values);
  const { plan, charts, tree, workload, binding } = installPlanImmutableSource(
    parent,
    publication,
    execution,
    inventory,
    images
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
    ]).size !== 7
  )
    throw new Error("INVALID_NODE_PLATFORM_IDENTITY");
  const body = {
    format: 1 as const,
    artifactId: form.artifactId,
    planId: plan.id,
    planDigest: plan.plan.planDigest,
    treeId: tree.id,
    treeDigest: tree.treeDigest,
    bindingId: binding.id,
    bindingDigest: binding.bindingDigest,
    nodePlatforms: parseNodePlatformScope(form.nodePlatforms),
    maximumImageBytes: form.maximumImageBytes,
    maximumMetadataBytes: form.maximumMetadataBytes,
    maximumBytes: form.maximumBytes,
    maximumObservationBytes: form.maximumObservationBytes,
    maximumHelperBytes: form.maximumHelperBytes,
    maximumCalls: Number(form.maximumCalls),
    maximumPairs: Number(form.maximumPairs),
    timeoutSeconds: Number(form.timeoutSeconds),
  };
  if (new TextEncoder().encode(JSON.stringify(body)).length > 1048576)
    throw new Error("REQUEST_TOO_LARGE");
  return body;
}

export function validateInstallPlanNodePlatforms(
  value: unknown,
  parent: NvkeInstallPlan,
  publication: NvkeInstallPlanCharts,
  execution: NvkeInstallPlanTree,
  inventory: NvkeInstallPlanWorkload,
  images: NvkeInstallPlanImageBinding,
  artifactId: string,
  limits?: NvkeInstallPlanNodePlatformsForm
) {
  const { plan, charts, tree, workload, binding } = installPlanImmutableSource(
    parent,
    publication,
    execution,
    inventory,
    images
  );
  const row = installPlanNodePlatformsSchema.parse(value);
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
    row.imageReferenceCount !== binding.imageCount ||
    Date.parse(row.observedAt) < Date.parse(binding.observedAt)
  )
    throw new Error("NODE_PLATFORM_SOURCE_CHANGED");
  if (limits) {
    const form = installPlanNodePlatformsFormSchema.parse(limits);
    if (
      row.nodeCount !== Object.keys(parseNodePlatformScope(form.nodePlatforms)).length ||
      row.pairCount > Number(form.maximumPairs)
    )
      throw new Error("NODE_PLATFORM_SCOPE_EXCEEDED");
  }
  return row;
}
