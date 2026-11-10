import { z } from "zod";
import type { NvkeInstallPlan } from "./operator-install-plan";
import type { NvkeInstallPlanCharts } from "./operator-install-plan-charts";
import type { NvkeInstallPlanTree } from "./operator-install-plan-tree";
import { installPlanImageFormSchema } from "./operator-install-plan-image";
import {
  installPlanWorkloadMetadataSchema,
  installPlanWorkloadSource,
  validateInstallPlanWorkload,
  validateInstallPlanWorkloadSource,
  type NvkeInstallPlanWorkload,
} from "./operator-install-plan-workload";
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
    .refine((value) => /^[1-9][0-9]{0,18}$/.test(value) && BigInt(value) <= BigInt(maximum));
const imageBindingsSchema = z
  .record(
    installPlanImageFormSchema.shape.reference,
    z
      .object({
        sourceKind: z.enum(["local", "registry"]),
        artifactId: guid,
        artifactDigest: digest,
      })
      .strict()
  )
  .refine((value) => Object.keys(value).length <= 10000);
export function parseImageProofBindings(text: string) {
  if (text.length > 1048576) throw new Error("INVALID_IMAGE_BINDINGS");
  const parsed: unknown = JSON.parse(text);
  const tokens =
    text.match(/"(?:\\.|[^"\\])*"|[{}\[\],:]|true|false|null|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/g) ??
    [];
  let offset = 0;
  const walk = (depth: number): void => {
    if (depth > 8) throw new Error("INVALID_IMAGE_BINDINGS");
    const token = tokens[offset++];
    if (token === "{") {
      const keys = new Set<string>();
      while (tokens[offset] !== "}") {
        const key: unknown = JSON.parse(tokens[offset++]);
        if (typeof key !== "string" || keys.has(key) || tokens[offset++] !== ":")
          throw new Error("INVALID_IMAGE_BINDINGS");
        keys.add(key);
        walk(depth + 1);
        if (tokens[offset] !== ",") break;
        offset++;
      }
      if (tokens[offset++] !== "}") throw new Error("INVALID_IMAGE_BINDINGS");
    } else if (token === "[") {
      while (tokens[offset] !== "]") {
        walk(depth + 1);
        if (tokens[offset] !== ",") break;
        offset++;
      }
      if (tokens[offset++] !== "]") throw new Error("INVALID_IMAGE_BINDINGS");
    }
  };
  walk(0);
  if (offset !== tokens.length) throw new Error("INVALID_IMAGE_BINDINGS");
  return Object.fromEntries(
    Object.entries(imageBindingsSchema.parse(parsed)).sort(([left], [right]) =>
      left < right ? -1 : left > right ? 1 : 0
    )
  );
}

export const installPlanImageBindingFormSchema = z
  .object({
    artifactId: guid,
    imageBindings: z
      .string()
      .max(1048576)
      .refine((value) => {
        try {
          parseImageProofBindings(value);
          return true;
        } catch {
          return false;
        }
      }),
    maximumBytes: decimal("67108864"),
    maximumImageBytes: decimal("9223372036854775807"),
    timeoutSeconds: decimal("300"),
  })
  .strict();
export type NvkeInstallPlanImageBindingForm = z.infer<typeof installPlanImageBindingFormSchema>;
export const installPlanImageBindingSchema = z
  .object({
    ...installPlanWorkloadMetadataSchema.shape,
    __typename: z.literal("NvkeOperatorInstallPlanImageBindingType").optional(),
    workloadId: guid,
    workloadVersion: version,
    bindingDigest: digest,
    imageCount: z.number().int().min(0).max(10000),
    importCount: z.number().int().min(0).max(10000),
    rewriteCount: z.number().int().min(0).max(10000),
    verifiedImageBytes: z
      .string()
      .refine(
        (value) =>
          /^(0|[1-9][0-9]{0,18})$/.test(value) && BigInt(value) < BigInt("9223372036854775808")
      ),
    workloadIssueCount: z.number().int().min(0).max(2147483647),
    executionState: z.literal(
      "requires-reviewed-operator-images-node-platform-credentials-rewrite-import-use-and-linked-admission"
    ),
  })
  .omit({ itemCount: true, resourceCount: true, imageReferenceCount: true, issueCount: true })
  .strict()
  .refine(
    (row) =>
      new Set([
        row.id,
        row.treeId,
        row.chartArtifactId,
        row.planId,
        row.operationId,
        row.workloadId,
      ]).size === 6 &&
      row.rewriteCount <= row.imageCount &&
      (row.imageCount === 0) === (BigInt(row.verifiedImageBytes) === BigInt(0))
  );
export type NvkeInstallPlanImageBinding = z.infer<typeof installPlanImageBindingSchema>;
export function installPlanImageBindingSource(
  parent: NvkeInstallPlan,
  publication: NvkeInstallPlanCharts,
  execution: NvkeInstallPlanTree,
  inventory: NvkeInstallPlanWorkload
) {
  const source = installPlanWorkloadSource(parent, publication, execution);
  const workload = validateInstallPlanWorkload(
    inventory,
    source.plan,
    source.charts,
    source.tree,
    inventory.id
  );
  return { ...source, workload };
}
export function installPlanImageBindingRequest(
  values: unknown,
  parent: NvkeInstallPlan,
  publication: NvkeInstallPlanCharts,
  execution: NvkeInstallPlanTree,
  inventory: NvkeInstallPlanWorkload
) {
  const form = installPlanImageBindingFormSchema.parse(values);
  const { plan, charts, tree, workload } = installPlanImageBindingSource(
    parent,
    publication,
    execution,
    inventory
  );
  const ids = new Set([
    form.artifactId,
    tree.id,
    charts.id,
    plan.id,
    plan.operationId,
    workload.id,
  ]);
  if (ids.size !== 6) throw new Error("INVALID_BINDING_IDENTITY");
  const imageBindings = parseImageProofBindings(form.imageBindings);
  for (const row of Object.values(imageBindings)) {
    if (ids.has(row.artifactId)) throw new Error("INVALID_IMAGE_IDENTITY");
  }
  const body = {
    format: 1 as const,
    artifactId: form.artifactId,
    planId: plan.id,
    planDigest: plan.plan.planDigest,
    treeId: tree.id,
    treeDigest: tree.treeDigest,
    workloadId: workload.id,
    workloadDigest: workload.workloadDigest,
    imageBindings,
    maximumBytes: form.maximumBytes,
    maximumImageBytes: form.maximumImageBytes,
    timeoutSeconds: Number(form.timeoutSeconds),
  };
  if (new TextEncoder().encode(JSON.stringify(body)).length > 1048576)
    throw new Error("REQUEST_TOO_LARGE");
  return body;
}
export function validateInstallPlanImageBinding(
  value: unknown,
  parent: NvkeInstallPlan,
  publication: NvkeInstallPlanCharts,
  execution: NvkeInstallPlanTree,
  inventory: NvkeInstallPlanWorkload,
  artifactId: string,
  limits?: Pick<NvkeInstallPlanImageBindingForm, "maximumImageBytes" | "imageBindings">
) {
  const { plan, charts, tree, workload } = installPlanImageBindingSource(
    parent,
    publication,
    execution,
    inventory
  );
  const row = installPlanImageBindingSchema.parse(value);
  validateInstallPlanWorkloadSource(row, plan, charts, tree, artifactId);
  if (
    row.workloadId !== workload.id ||
    row.workloadVersion !== workload.version ||
    row.workloadDigest !== workload.workloadDigest ||
    row.imageCount < workload.imageReferenceCount ||
    row.imageUseCount !== workload.imageUseCount ||
    row.workloadIssueCount !== workload.issueCount ||
    Date.parse(row.observedAt) < Date.parse(workload.observedAt)
  )
    throw new Error("BINDING_SOURCE_CHANGED");
  if (
    limits &&
    (BigInt(row.verifiedImageBytes) >
      BigInt(decimal("9223372036854775807").parse(limits.maximumImageBytes)) ||
      row.imageCount !== Object.keys(parseImageProofBindings(limits.imageBindings)).length)
  )
    throw new Error("BINDING_BUDGET_EXCEEDED");
  return row;
}
