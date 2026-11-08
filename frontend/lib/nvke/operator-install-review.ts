import { z } from "zod";
import { validNvkeGuid } from "./recovery";
const guid = z.string().refine(validNvkeGuid);
const version = z.number().int().positive().safe();
const digest = z.string().regex(/^[0-9a-f]{64}$/);
const timestamp = z.string().datetime({ offset: true });
const counter = z
  .string()
  .regex(/^(0|[1-9][0-9]{0,18})$/)
  .refine(
    (value) => /^(0|[1-9][0-9]{0,18})$/.test(value) && BigInt(value) < BigInt("9223372036854775808")
  );
const name = z.string().regex(/^[a-z0-9][a-z0-9.-]{0,252}$/);
export const installWorkloadSchema = z
  .object({
    namespace: name,
    kind: z.enum(["Deployment", "StatefulSet"]),
    name,
    generation: counter,
    observedGeneration: counter.nullable(),
    desiredReplicas: counter,
    readyReplicas: counter.nullable(),
    updatedReplicas: counter.nullable(),
    observation: z.enum(["ready", "requires-review"]),
  })
  .refine(
    (row) =>
      row.observation !== "ready" ||
      (row.observedGeneration !== null &&
        counter.safeParse(row.generation).success &&
        counter.safeParse(row.observedGeneration).success &&
        BigInt(row.observedGeneration) >= BigInt(row.generation) &&
        row.desiredReplicas !== "0" &&
        row.readyReplicas === row.desiredReplicas &&
        row.updatedReplicas === row.desiredReplicas)
  );
export type NvkeInstallWorkload = z.infer<typeof installWorkloadSchema>;
export const installWorkloadId = (row: Pick<NvkeInstallWorkload, "namespace" | "kind" | "name">) =>
  JSON.stringify([row.namespace, row.kind, row.name]);
export const installReviewSchema = z
  .object({
    snapshotId: guid,
    snapshotExpiresAt: timestamp,
    workloadCount: z.number().int().min(1).max(10000),
    siteId: guid,
    siteVersion: version,
    operationId: guid,
    operationVersion: version,
    authorityId: guid,
    authorityVersion: version,
    instanceId: guid,
    bundleManifestSha256: digest,
    nativeRevision: z.string().regex(/^[0-9a-f]{40}$/),
    nativeRunId: guid,
    reviewDigest: digest,
    observedAt: timestamp,
    originalStartedAt: timestamp,
    nativeArgumentsSha256: digest,
    nativeExitCode: z
      .number()
      .int()
      .safe()
      .refine((value) => value !== 0),
    apiReady: z.literal(true),
    continuationAuthorized: z.literal(false),
    reviewerSha256: digest,
    qualificationHelperSha256: digest,
    evidence: z.object({
      guardSha256: digest,
      startedSha256: digest,
      finishedSha256: digest,
      stdoutSha256: digest,
      stderrSha256: digest,
    }),
  })
  .refine(
    (review) =>
      Date.parse(review.originalStartedAt) <= Date.parse(review.observedAt) &&
      Date.parse(review.observedAt) < Date.parse(review.snapshotExpiresAt)
  );
export type NvkeInstallReview = z.infer<typeof installReviewSchema>;
export const installWorkloadPageSchema = z
  .object({
    snapshotId: guid,
    operationId: guid,
    operationVersion: version,
    reviewDigest: digest,
    items: z
      .array(installWorkloadSchema)
      .max(100)
      .refine((rows) => new Set(rows.map(installWorkloadId)).size === rows.length),
    totalCount: z.number().int().min(0).max(10000),
    nextCursor: z.string().min(1).max(8192).nullable(),
  })
  .refine(
    (page) =>
      page.items.length <= page.totalCount && (page.nextCursor === null || page.items.length > 0)
  );
