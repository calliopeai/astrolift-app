import { z } from "zod";
import { validNvkeGuid } from "./recovery";
import { validOperatorArtifact } from "./operator-artifact";
const guid = z.string().refine(validNvkeGuid),
  version = z.number().int().positive().safe();
const digest = z.string().regex(/^[0-9a-f]{64}$/);
export const operatorJobPageSchema = z
  .object({
    siteId: guid,
    siteVersion: version,
    operationId: guid,
    operationVersion: version,
    authorityId: guid,
    authorityVersion: version,
    instanceId: guid,
    bundleManifestSha256: digest,
    nativeRevision: z.string().regex(/^[0-9a-f]{40}$/),
    command: z.string().regex(/^[a-z][a-z0-9.-]{0,119}$/),
    found: z.boolean(),
    state: z.enum([
      "not_found",
      "preparing",
      "queued",
      "dispatching",
      "running",
      "completed",
      "failed",
      "cancelled",
      "uncertain",
    ]),
    effectful: z.boolean(),
    cancellationRequested: z.boolean(),
    nativeExitCode: z.number().int().safe().nullable(),
    errorCode: z
      .string()
      .regex(/^[A-Z][A-Z0-9_]{0,63}$/)
      .nullable(),
    reconciledFromNativeReceipt: z.boolean(),
    receiptSha256: digest,
    items: z
      .array(
        z
          .object({ name: z.string(), size: z.string(), sha256: digest })
          .refine(validOperatorArtifact)
      )
      .max(100)
      .refine((rows) => new Set(rows.map((row) => row.name)).size === rows.length),
    nextCursor: z.string().min(1).max(8192).nullable(),
    totalCount: z.number().int().min(0).max(2048),
  })
  .refine(
    (page) =>
      page.items.length <= page.totalCount &&
      (page.found
        ? page.state !== "not_found"
        : page.state === "not_found" &&
          page.totalCount === 0 &&
          page.items.length === 0 &&
          page.nextCursor === null) &&
      (page.state !== "completed" || page.nativeExitCode === 0)
  );
export type NvkeOperatorJobPage = z.infer<typeof operatorJobPageSchema>;
