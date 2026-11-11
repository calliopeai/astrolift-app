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

export const PULL_AUTHORITY_STATE =
  "requires-pod-ownership-kubelet-credential-registry-correspondence-and-linked-admission";
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
const namePattern = /^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?(?:\.[a-z0-9](?:[-a-z0-9]*[a-z0-9])?)*$/;
const podKey = z.string().refine((value) => {
  const parts = value.split("/");
  return (
    parts.length === 2 &&
    value.trim() === value &&
    parts[0].length >= 1 &&
    parts[0].length <= 63 &&
    !parts[0].includes(".") &&
    parts[1].length >= 1 &&
    parts[1].length <= 253 &&
    parts.every((part) => namePattern.test(part))
  );
});
const uid = z
  .string()
  .refine((value) => value.trim() === value && /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(value));
const scopeSchema = z.record(podKey, uid).refine((value) => {
  const rows = Object.values(value);
  return rows.length > 0 && rows.length <= 10000 && new Set(rows).size === rows.length;
});

export function parsePullAuthorityScope(text: string) {
  if (new TextEncoder().encode(text).length > 1048576) throw new Error("INVALID_POD_SCOPE");
  const parsed: unknown = JSON.parse(text);
  const tokens =
    text.match(/"(?:\\.|[^"\\])*"|[{}\[\],:]|true|false|null|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/g) ??
    [];
  let offset = 0;
  const walk = (depth: number): void => {
    if (depth > 8) throw new Error("INVALID_POD_SCOPE");
    const token = tokens[offset++];
    if (token === "{") {
      const keys = new Set<string>();
      while (tokens[offset] !== "}") {
        const key: unknown = JSON.parse(tokens[offset++]);
        if (typeof key !== "string" || keys.has(key) || tokens[offset++] !== ":")
          throw new Error("INVALID_POD_SCOPE");
        keys.add(key);
        walk(depth + 1);
        if (tokens[offset] !== ",") break;
        offset++;
      }
      if (tokens[offset++] !== "}") throw new Error("INVALID_POD_SCOPE");
    } else if (token === "[") {
      while (tokens[offset] !== "]") {
        walk(depth + 1);
        if (tokens[offset] !== ",") break;
        offset++;
      }
      if (tokens[offset++] !== "]") throw new Error("INVALID_POD_SCOPE");
    }
  };
  walk(0);
  if (offset !== tokens.length) throw new Error("INVALID_POD_SCOPE");
  return Object.fromEntries(
    Object.entries(scopeSchema.parse(parsed)).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
  );
}

export const installPlanPullAuthorityFormSchema = z
  .object({
    artifactId: guid,
    selectedPods: z.string().max(1048576),
    maximumBytes: decimal("67108864"),
    maximumCalls: decimal("32768"),
    timeoutSeconds: decimal("300"),
  })
  .strict()
  .superRefine((value, ctx) => {
    try {
      const scope = parsePullAuthorityScope(value.selectedPods);
      if (Number(value.maximumCalls) < 2 * Object.keys(scope).length + 2)
        ctx.addIssue({
          code: "custom",
          path: ["maximumCalls"],
          message: "INSUFFICIENT_READ_BUDGET",
        });
    } catch {
      ctx.addIssue({ code: "custom", path: ["selectedPods"], message: "INVALID_POD_SCOPE" });
    }
  });
export type NvkeInstallPlanPullAuthorityForm = z.infer<typeof installPlanPullAuthorityFormSchema>;

const baseFields = z.object(installPlanWorkloadMetadataSchema.shape).omit({
  __typename: true,
  itemCount: true,
  resourceCount: true,
  imageUseCount: true,
  imageReferenceCount: true,
  issueCount: true,
  executionState: true,
}).shape;
export const installPlanPullAuthoritySchema = z
  .object({
    ...baseFields,
    __typename: z.literal("NvkeOperatorInstallPlanPullAuthorityType").optional(),
    workloadId: guid,
    workloadVersion: version,
    bindingId: guid,
    bindingVersion: version,
    bindingDigest: digest,
    authorityDigest: digest,
    podCount: z.number().int().min(1).max(10000),
    serviceAccountCount: z.number().int().min(0).max(32768),
    secretCount: z.number().int().min(0).max(32768),
    missingReferenceCount: z.number().int().min(0).max(32768),
    executionState: z.literal(PULL_AUTHORITY_STATE),
  })
  .strict()
  .refine(
    (row) =>
      row.serviceAccountCount <= row.podCount &&
      row.missingReferenceCount <= row.serviceAccountCount + row.secretCount &&
      2 * (row.podCount + row.serviceAccountCount + row.secretCount) + 2 <= 32768
  );
export type NvkeInstallPlanPullAuthority = z.infer<typeof installPlanPullAuthoritySchema>;

export function installPlanPullAuthorityRequest(
  values: unknown,
  parent: NvkeInstallPlan,
  publication: NvkeInstallPlanCharts,
  execution: NvkeInstallPlanTree,
  inventory: NvkeInstallPlanWorkload,
  images: NvkeInstallPlanImageBinding
) {
  const form = installPlanPullAuthorityFormSchema.parse(values);
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
    throw new Error("INVALID_PULL_AUTHORITY_IDENTITY");
  const body = {
    format: 1 as const,
    artifactId: form.artifactId,
    planId: plan.id,
    planDigest: plan.plan.planDigest,
    treeId: tree.id,
    treeDigest: tree.treeDigest,
    bindingId: binding.id,
    bindingDigest: binding.bindingDigest,
    selectedPods: parsePullAuthorityScope(form.selectedPods),
    maximumBytes: form.maximumBytes,
    maximumCalls: Number(form.maximumCalls),
    timeoutSeconds: Number(form.timeoutSeconds),
  };
  if (new TextEncoder().encode(JSON.stringify(body)).length > 1048576)
    throw new Error("REQUEST_TOO_LARGE");
  return body;
}

export function validateInstallPlanPullAuthority(
  value: unknown,
  parent: NvkeInstallPlan,
  publication: NvkeInstallPlanCharts,
  execution: NvkeInstallPlanTree,
  inventory: NvkeInstallPlanWorkload,
  images: NvkeInstallPlanImageBinding,
  artifactId: string,
  limits?: NvkeInstallPlanPullAuthorityForm
) {
  const { plan, charts, tree, workload, binding } = installPlanImmutableSource(
    parent,
    publication,
    execution,
    inventory,
    images
  );
  const row = installPlanPullAuthoritySchema.parse(value);
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
    Date.parse(row.observedAt) < Date.parse(binding.observedAt)
  )
    throw new Error("PULL_AUTHORITY_SOURCE_CHANGED");
  if (limits) {
    const form = installPlanPullAuthorityFormSchema.parse(limits);
    if (
      row.podCount !== Object.keys(parsePullAuthorityScope(form.selectedPods)).length ||
      2 * (row.podCount + row.serviceAccountCount + row.secretCount) + 2 > Number(form.maximumCalls)
    )
      throw new Error("PULL_AUTHORITY_SCOPE_EXCEEDED");
  }
  return row;
}
