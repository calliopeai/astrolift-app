import { z } from "zod";
import { validNvkeGuid } from "./recovery";

const id = z.string().refine(validNvkeGuid);
const version = z.number().int().positive().max(Number.MAX_SAFE_INTEGER);
const count = z
  .string()
  .refine(
    (value) =>
      /^(0|[1-9][0-9]{0,18})$/.test(value) && BigInt(value) <= BigInt("9223372036854775807")
  );
const generation = count.refine((value) => value !== "0").nullable();
const date = z
  .string()
  .regex(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})$/)
  .refine((value) => Number.isFinite(Date.parse(value)));
const name = z
  .string()
  .max(253)
  .regex(/^[a-z0-9](?:[-a-z0-9.]*[a-z0-9])?$/);
const namespace = z
  .string()
  .max(63)
  .regex(/^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$/);
const credentials = z
  .object({
    health: z.enum([
      "HEALTHY",
      "FAILING",
      "UNKNOWN",
      "STALE",
      "PENDING",
      "NO-IDENTITY",
      "UNSUPPORTED",
      "UNREADABLE",
    ]),
    reason: z.enum([
      "identity_api_not_served",
      "identity_list_forbidden",
      "identity_list_unavailable",
      "no_identity_observes_secret",
      "invalid_last_checked_time",
      "last_checked_time_in_future",
      "provider_check_overdue",
      "provider_manager_not_found",
      "identity_outside_manager_namespace",
      "identity_outside_manager_filter",
      "provider_manager_unavailable",
      "current_generation_not_checked",
      "provider_check_succeeded",
      "provider_check_failed",
      "provider_check_unknown",
    ]),
    identityNamespace: namespace.nullable(),
    identityName: name.nullable(),
    identityGeneration: generation,
    readyStatus: z.enum(["True", "False", "Unknown"]).nullable(),
    observedGeneration: generation,
    lastCheckedTime: date.nullable(),
    nextCheckTime: date.nullable(),
    additionalIdentityCount: z.number().int().min(0).max(10000),
  })
  .refine((value) => (value.identityName === null) === (value.identityNamespace === null))
  .refine(
    (value) =>
      !["HEALTHY", "FAILING"].includes(value.health) ||
      (value.identityName !== null &&
        value.identityGeneration !== null &&
        value.identityGeneration === value.observedGeneration &&
        value.lastCheckedTime !== null &&
        value.readyStatus === (value.health === "HEALTHY" ? "True" : "False") &&
        value.reason ===
          (value.health === "HEALTHY" ? "provider_check_succeeded" : "provider_check_failed"))
  );
const provider = z
  .object({
    provider: z.enum(["nico", "launchlayer"]),
    state: z.enum(["NOT-INSTALLED", "NOT-CONFIGURED", "DEGRADED", "CONFIGURED", "UNKNOWN"]),
    reason: z.enum([
      "provider_crd_inventory_unreadable",
      "provider_crds_not_established",
      "provider_manager_inventory_unreadable",
      "provider_secret_reference_invalid",
      "provider_secret_reference_absent",
      "provider_secret_presence_unreadable",
      "provider_secret_absent",
      "provider_endpoint_absent",
      "provider_manager_not_found",
      "provider_manager_unavailable",
      "provider_configuration_present",
    ]),
    complete: z.boolean(),
    crdsEstablished: z.boolean().nullable(),
    controllerAvailable: z.boolean().nullable(),
    managerNamespace: namespace.nullable(),
    managerName: name.nullable(),
    readyReplicas: count.nullable(),
    desiredReplicas: count.nullable(),
    secretNamespace: namespace.nullable(),
    secretName: name.nullable(),
    secretPresent: z.boolean().nullable(),
    endpointConfigured: z.boolean().nullable(),
    unconfiguredIsValid: z.boolean(),
    credentials: credentials.nullable(),
  })
  .refine(
    (value) =>
      value.unconfiguredIsValid === (value.provider === "launchlayer") &&
      (value.provider !== "launchlayer" || value.credentials === null)
  )
  .refine(
    (value) =>
      value.state !== "CONFIGURED" ||
      (value.crdsEstablished === true &&
        value.controllerAvailable === true &&
        value.secretPresent === true &&
        value.managerName !== null &&
        value.managerNamespace !== null &&
        value.secretName !== null &&
        value.secretNamespace !== null &&
        value.endpointConfigured !== false)
  )
  .refine(
    (value) =>
      value.state !== "NOT-INSTALLED" ||
      (value.crdsEstablished === false && value.credentials === null)
  )
  .refine((value) => value.state !== "UNKNOWN" || !value.complete);
export const nvkeProviderSourceSchema = z.object({
  connectionId: id,
  connectionVersion: version,
  siteVersion: version,
  upstreamRevision: z.string().regex(/^[0-9a-f]{40}$/),
});
export const nvkeProviderObservationSchema = z
  .object({
    siteId: id,
    operationId: id,
    operationVersion: version,
    sourceSiteVersion: version,
    connectionId: id,
    connectionVersion: version,
    upstreamRevision: z.string().regex(/^[0-9a-f]{40}$/),
    observedAt: date,
    complete: z.boolean(),
    providers: z.array(provider).length(2),
  })
  .refine(
    (value) =>
      value.providers.length === 2 &&
      value.providers[0].provider === "nico" &&
      value.providers[1].provider === "launchlayer"
  )
  .refine((value) => value.complete === value.providers.every((row) => row.complete));
export const nvkeProviderIdentityPageSchema = z
  .object({
    siteId: id,
    operationId: id,
    operationVersion: version,
    observedAt: date,
    provider: z.enum(["nico", "launchlayer"]),
    items: z.array(z.object({ key: z.string(), namespace, name })).max(100),
    totalCount: z.number().int().min(0).max(10000),
    nextCursor: z.string().min(1).max(16384).nullable(),
  })
  .refine(
    (value) =>
      value.items.length <= value.totalCount &&
      (value.nextCursor === null || value.items.length > 0) &&
      new Set(value.items.map((row) => row.key)).size === value.items.length &&
      value.items.every((row) => row.key === row.namespace + "/" + row.name)
  );
