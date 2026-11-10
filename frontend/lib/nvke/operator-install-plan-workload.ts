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
const bytes = decimal("67108864");

const contextBindingsSchema = z
  .record(
    z.string().regex(/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/),
    z.object({ contextId: guid, contextDigest: digest }).strict()
  )
  .refine((value) => Object.keys(value).length <= 10000);

export function parseWorkloadContextBindings(text: string) {
  if (text.length > 1048576) throw new Error("INVALID_CONTEXT_BINDINGS");
  const parsed: unknown = JSON.parse(text);
  const tokens =
    text.match(/"(?:\\.|[^"\\])*"|[{}\[\],:]|true|false|null|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/g) ??
    [];
  let offset = 0;
  const walk = (depth: number): void => {
    if (depth > 8) throw new Error("INVALID_CONTEXT_BINDINGS");
    const token = tokens[offset++];
    if (token === "{") {
      const keys = new Set<string>();
      while (tokens[offset] !== "}") {
        const key: unknown = JSON.parse(tokens[offset++]);
        if (typeof key !== "string" || keys.has(key) || tokens[offset++] !== ":")
          throw new Error("INVALID_CONTEXT_BINDINGS");
        keys.add(key);
        walk(depth + 1);
        if (tokens[offset] !== ",") break;
        offset++;
      }
      if (tokens[offset++] !== "}") throw new Error("INVALID_CONTEXT_BINDINGS");
    } else if (token === "[") {
      while (tokens[offset] !== "]") {
        walk(depth + 1);
        if (tokens[offset] !== ",") break;
        offset++;
      }
      if (tokens[offset++] !== "]") throw new Error("INVALID_CONTEXT_BINDINGS");
    }
  };
  walk(0);
  if (offset !== tokens.length) throw new Error("INVALID_CONTEXT_BINDINGS");
  return Object.fromEntries(
    Object.entries(contextBindingsSchema.parse(parsed)).sort(([left], [right]) =>
      left < right ? -1 : left > right ? 1 : 0
    )
  );
}

export const installPlanWorkloadFormSchema = z
  .object({
    artifactId: guid,
    contextBindings: z
      .string()
      .max(1048576)
      .refine((value) => {
        try {
          parseWorkloadContextBindings(value);
          return true;
        } catch {
          return false;
        }
      }),
    maximumBytes: bytes,
    maximumResources: decimal("10000"),
    timeoutSeconds: decimal("300"),
  })
  .strict();
export type NvkeInstallPlanWorkloadForm = z.infer<typeof installPlanWorkloadFormSchema>;

export const installPlanWorkloadMetadataSchema = z
  .object({
    __typename: z.literal("NvkeOperatorInstallPlanWorkloadType").optional(),
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
    artifactDigest: digest,
    sourceDigest: digest,
    captureDigest: digest,
    workloadDigest: digest,
    itemCount: z.number().int().min(1).max(10000),
    resourceCount: z.number().int().min(0).max(10000),
    imageUseCount: z.number().int().min(0).max(2147483647),
    imageReferenceCount: z.number().int().min(0).max(2147483647),
    issueCount: z.number().int().min(0).max(2147483647),
    observedAt: z
      .string()
      .max(40)
      .datetime({ offset: true })
      .refine((value) => value.trim() === value && Number.isFinite(Date.parse(value))),
    executionState: z.literal(
      "requires-reviewed-operator-images-platform-credentials-rewrite-import-use-and-linked-admission"
    ),
    continuationAuthorized: z.literal(false),
  })
  .strict()
  .refine(
    (row) =>
      new Set([row.id, row.treeId, row.chartArtifactId, row.planId, row.operationId]).size === 5
  );
export const installPlanWorkloadSchema = installPlanWorkloadMetadataSchema.refine(
  (row) => row.imageReferenceCount <= row.imageUseCount
);
export type NvkeInstallPlanWorkload = z.infer<typeof installPlanWorkloadSchema>;

export function installPlanWorkloadSource(
  parent: NvkeInstallPlan,
  publication: NvkeInstallPlanCharts,
  execution: NvkeInstallPlanTree
) {
  const plan = installPlanSchema.parse(parent);
  const charts = validateInstallPlanCharts(publication, plan, publication.id);
  const tree = validateInstallPlanTree(execution, plan, charts, execution.id);
  return { plan, charts, tree };
}

export function installPlanWorkloadRequest(
  values: unknown,
  parent: NvkeInstallPlan,
  publication: NvkeInstallPlanCharts,
  execution: NvkeInstallPlanTree
) {
  const form = installPlanWorkloadFormSchema.parse(values);
  const { plan, charts, tree } = installPlanWorkloadSource(parent, publication, execution);
  if (new Set([form.artifactId, tree.id, charts.id, plan.id, plan.operationId]).size !== 5)
    throw new Error("INVALID_IMAGE_IDENTITY");
  const contextBindings = parseWorkloadContextBindings(form.contextBindings);
  const ids = new Set([form.artifactId, tree.id, charts.id, plan.id, plan.operationId]);
  for (const row of Object.values(contextBindings)) {
    if (ids.has(row.contextId)) throw new Error("INVALID_CONTEXT_IDENTITY");
    ids.add(row.contextId);
  }
  const body = {
    format: 1 as const,
    artifactId: form.artifactId,
    planId: plan.id,
    planDigest: plan.plan.planDigest,
    treeId: tree.id,
    treeDigest: tree.treeDigest,
    contextBindings,
    maximumBytes: form.maximumBytes,
    maximumResources: Number(form.maximumResources),
    timeoutSeconds: Number(form.timeoutSeconds),
  };
  if (new TextEncoder().encode(JSON.stringify(body)).length > 1048576)
    throw new Error("REQUEST_TOO_LARGE");
  return body;
}

export function validateInstallPlanWorkload(
  value: unknown,
  parent: NvkeInstallPlan,
  publication: NvkeInstallPlanCharts,
  execution: NvkeInstallPlanTree,
  artifactId: string,
  limits?: Pick<NvkeInstallPlanWorkloadForm, "maximumResources">
) {
  const row = installPlanWorkloadSchema.parse(value);
  validateInstallPlanWorkloadSource(row, parent, publication, execution, artifactId);
  if (
    row.itemCount !== parent.itemCount ||
    (limits && row.resourceCount > Number(decimal("10000").parse(limits.maximumResources)))
  )
    throw new Error("IMAGE_BUDGET_EXCEEDED");
  return row;
}

export function validateInstallPlanWorkloadSource(
  row: Pick<
    z.infer<typeof installPlanWorkloadMetadataSchema>,
    | "id"
    | "planId"
    | "planVersion"
    | "planDigest"
    | "chartArtifactId"
    | "chartArtifactVersion"
    | "chartArtifactDigest"
    | "treeId"
    | "treeVersion"
    | "treeDigest"
    | "operationId"
    | "operationVersion"
    | "siteId"
    | "siteVersion"
    | "authorityId"
    | "authorityVersion"
  >,
  parent: NvkeInstallPlan,
  publication: NvkeInstallPlanCharts,
  execution: NvkeInstallPlanTree,
  artifactId: string
) {
  const { plan, charts, tree } = installPlanWorkloadSource(parent, publication, execution);
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
}
