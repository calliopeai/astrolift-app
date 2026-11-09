import { z } from "zod";
import { validNvkeGuid } from "./recovery";
import { installWorkloadId, installWorkloadSchema } from "./operator-install-review";

const guid = z.string().refine(validNvkeGuid);
const version = z.number().int().positive().safe();
const digest = z.string().regex(/^[0-9a-f]{64}$/);
const timestamp = z.string().datetime({ offset: true });
const count = z.number().int().min(0).max(10000);
const bytes = z
  .string()
  .regex(/^(0|[1-9][0-9]{0,18})$/)
  .refine(
    (value) =>
      /^(0|[1-9][0-9]{0,18})$/.test(value) && BigInt(value) <= BigInt("9223372036854775807")
  );
export const installPlanSchema = z
  .object({
    id: guid,
    version,
    status: z.literal("prepared"),
    operationId: guid,
    operationVersion: version,
    siteId: guid,
    siteVersion: version,
    authorityId: guid,
    authorityVersion: version,
    itemCount: count.refine((value) => value > 0),
    retainedBytes: bytes,
    proposedBytes: bytes,
    plan: z.object({
      format: z.literal(1),
      scope: z.literal("remaining-management-install-proposal"),
      id: guid,
      preparedAt: timestamp,
      phaseEvidence: z.literal("retained-native-stderr-order-only"),
      requiresOriginalSourceVerification: z.literal(true),
      requiresExplicitAcknowledgement: z.literal(true),
      continuationAuthorized: z.literal(false),
      firstRemainingOrdinal: count,
      planDigest: digest,
      source: z.object({
        siteId: guid,
        instanceId: guid,
        jobId: guid,
        nativeRunId: guid,
        nativeRevision: z.string().regex(/^[0-9a-f]{40}$/),
        nativeExitCode: z
          .number()
          .int()
          .min(-2147483648)
          .max(2147483647)
          .refine((value) => value !== 0),
        bundleManifestSha256: digest,
        nativeArgumentsSha256: digest,
        retainedInputsSha256: digest,
        proposedInputsSha256: digest,
        proposedExecutionSha256: digest,
        privateSourceSha256: digest,
        reviewDigest: digest,
        stderrSha256: digest,
        originalStartedAt: timestamp,
        reviewObservedAt: timestamp,
        originalDeploymentEvidence: z.enum([
          "retained-prepared-deployment",
          "reconstructed-prepared-deployment",
        ]),
      }),
    }),
    infrastructure: z.object({
      apiReady: z.literal(true),
      observedAt: timestamp,
      clusterUid: guid,
      workloadCount: count,
    }),
  })
  .refine(
    (row) =>
      row.id === row.plan.id &&
      row.siteId === row.plan.source.siteId &&
      row.operationId === row.plan.source.jobId &&
      row.plan.firstRemainingOrdinal < row.itemCount &&
      Date.parse(row.plan.source.originalStartedAt) <=
        Date.parse(row.plan.source.reviewObservedAt) &&
      Date.parse(row.plan.source.reviewObservedAt) <= Date.parse(row.plan.preparedAt) &&
      Date.parse(row.infrastructure.observedAt) === Date.parse(row.plan.source.reviewObservedAt)
  );
export type NvkeInstallPlan = z.infer<typeof installPlanSchema>;
const itemType = z.enum(["manifest", "template", "helm"]);
export const installPlanItemSchema = z.object({
  ordinal: count,
  name: z.string().regex(/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/),
  type: itemType,
  originalType: itemType,
  contentSha256: digest,
  originalContentSha256: digest,
  originalObservation: z.enum([
    "returned-before-next-observed-item",
    "last-observed-in-unsuccessful-run",
    "not-observed",
  ]),
  proposedAction: z.enum(["skip", "execute"]),
});
export type NvkeInstallPlanItem = z.infer<typeof installPlanItemSchema>;
const pageFields = {
  planId: guid,
  planDigest: digest,
  nextCursor: z.string().min(1).max(16384).nullable(),
  totalCount: count,
};
export const installPlanItemPageSchema = z.object({
  ...pageFields,
  view: z.literal("items"),
  items: z
    .array(installPlanItemSchema)
    .max(100)
    .refine((rows) => new Set(rows.map((row) => row.name)).size === rows.length),
});
export const installPlanWorkloadPageSchema = z.object({
  ...pageFields,
  view: z.literal("workloads"),
  items: z
    .array(installWorkloadSchema)
    .max(100)
    .refine((rows) => new Set(rows.map(installWorkloadId)).size === rows.length),
});
export interface NvkeInstallPlanSource {
  siteId: string;
  siteVersion: number;
  operationId: string;
  operationVersion: number;
  authorityId: string;
  authorityVersion: number;
  instanceId: string;
  bundleManifestSha256: string;
  nativeRevision: string;
}
export function installPlanMatches(plan: NvkeInstallPlan, source: NvkeInstallPlanSource) {
  return (
    plan.siteId === source.siteId &&
    plan.siteVersion === source.siteVersion &&
    plan.operationId === source.operationId &&
    plan.operationVersion === source.operationVersion &&
    plan.authorityId === source.authorityId &&
    plan.authorityVersion === source.authorityVersion &&
    plan.plan.source.instanceId === source.instanceId &&
    plan.plan.source.bundleManifestSha256 === source.bundleManifestSha256 &&
    plan.plan.source.nativeRevision === source.nativeRevision
  );
}
export function validateInstallPlanPage(
  value: unknown,
  plan: NvkeInstallPlan,
  view: "items" | "workloads",
  limit: number,
  search: string,
  initial: boolean
) {
  const page = (view === "items" ? installPlanItemPageSchema : installPlanWorkloadPageSchema).parse(
    value
  );
  const maximum = view === "items" ? plan.itemCount : plan.infrastructure.workloadCount;
  if (
    !Number.isInteger(limit) ||
    limit < 1 ||
    limit > 100 ||
    Array.from(search).length > 200 ||
    page.planId !== plan.id ||
    page.planDigest !== plan.plan.planDigest ||
    page.totalCount > maximum ||
    (!search && page.totalCount !== maximum) ||
    page.items.length > limit ||
    page.items.length > page.totalCount ||
    (page.totalCount > 0 && page.items.length === 0) ||
    (page.nextCursor !== null && (page.items.length !== limit || page.totalCount <= limit)) ||
    (initial &&
      (page.items.length !== Math.min(limit, page.totalCount) ||
        (page.nextCursor !== null) !== page.totalCount > limit))
  )
    throw new Error("INVALID_PLAN_PAGE");
  if (page.view === "items") {
    let previous = -1;
    for (const row of page.items) {
      const first = plan.plan.firstRemainingOrdinal;
      const observation =
        row.ordinal < first
          ? "returned-before-next-observed-item"
          : row.ordinal === first
            ? "last-observed-in-unsuccessful-run"
            : "not-observed";
      if (
        row.ordinal <= previous ||
        row.ordinal >= maximum ||
        (!search &&
          ((initial && previous === -1 && row.ordinal !== 0) ||
            (previous >= 0 && row.ordinal !== previous + 1))) ||
        row.originalObservation !== observation ||
        row.proposedAction !== (row.ordinal < first ? "skip" : "execute")
      )
        throw new Error("INVALID_PLAN_ITEM");
      previous = row.ordinal;
    }
  }
  return page;
}

export const installPlanFormSchema = z
  .object({ planId: guid, requestText: z.string().min(1) })
  .superRefine((form, ctx) => {
    try {
      // Validate the envelope without reserializing the private native request.
      if (new TextEncoder().encode(form.requestText).length > 1048576) throw new Error();
      const body = z
        .object({
          format: z.literal(1),
          planId: guid,
          request: z.record(z.string(), z.unknown()).nullable(),
          maximumInputBytes: bytes.refine((value) => value !== "0"),
          overrides: z.unknown().optional(),
        })
        .strict()
        .parse(JSON.parse(form.requestText));
      if (body.planId !== form.planId) throw new Error();
    } catch {
      ctx.addIssue({ code: "custom", path: ["requestText"], message: "INVALID_PLAN_INPUT" });
    }
  });
export type NvkeInstallPlanForm = z.infer<typeof installPlanFormSchema>;
