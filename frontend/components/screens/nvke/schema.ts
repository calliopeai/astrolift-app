import { z } from "zod";

export const createSiteSchema = z.object({
  name: z.string().trim().min(1).max(200),
  slug: z
    .string()
    .regex(/^[a-z0-9](?:[-a-z0-9]{0,198}[a-z0-9])?$/)
    .max(200),
  organizationNamespace: z.string().regex(/^[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?$/),
});
export const connectionSchema = z
  .object({
    surface: z.enum(["CONTROLLER", "TENANT", "OPERATOR", "DATASTORE"]),
    endpoint: z.string().url().max(2048),
    timeoutSeconds: z.number().int().min(1).max(300),
    allowHttp: z.boolean(),
    credentialMode: z.enum(["preserve", "replace", "clear"]),
    token: z.string().max(16384),
    caPem: z.string().max(262144),
    clientCertificatePem: z.string().max(262144),
    clientKeyPem: z.string().max(262144),
  })
  .refine(
    (v) =>
      v.credentialMode !== "replace" || Boolean(v.clientCertificatePem) === Boolean(v.clientKeyPem)
  );
export const nativeRequestSchema = z.object({
  operationKey: z.string().min(1).max(128),
  timeoutSeconds: z.number().int().min(30).max(86400),
  parametersText: z
    .string()
    .min(2)
    .max(16 * 1024 * 1024)
    .refine((text) => {
      try {
        const value: unknown = JSON.parse(text);
        return value !== null && typeof value === "object" && !Array.isArray(value);
      } catch {
        return false;
      }
    }),
});

export const spireServerSchema = z.object({
  address: z.string().min(1),
  port: z.number().int().min(1).max(65535),
  timeoutSeconds: z.number().int().min(30).max(86400),
});

export const temporalAuthoritySchema = z
  .object({
    address: z.string().min(1).max(512),
    namespace: z.string().min(1).max(255),
    taskQueue: z.string().min(1).max(255),
    serverName: z.string().max(253),
    useTls: z.boolean(),
    timeoutSeconds: z.number().int().min(1).max(300),
    credentialMode: z.enum(["preserve", "replace", "clear"]),
    apiKey: z.string().max(16384),
    caPem: z.string().max(262144),
    clientCertificatePem: z.string().max(262144),
    clientKeyPem: z.string().max(262144),
  })
  .refine(
    (v) =>
      v.credentialMode !== "replace" || Boolean(v.clientCertificatePem) === Boolean(v.clientKeyPem)
  )
  .refine((v) => v.useTls || (!v.serverName && v.credentialMode !== "replace"));

export const metricsAuthoritySchema = z
  .object(connectionSchema.shape)
  .omit({ surface: true })
  .refine(
    (v) =>
      v.credentialMode !== "replace" || Boolean(v.clientCertificatePem) === Boolean(v.clientKeyPem)
  )
  .refine((v) => {
    try {
      const url = new URL(v.endpoint);
      return (
        (url.protocol === "https:" || (v.allowHttp && url.protocol === "http:")) &&
        !url.username &&
        !url.password &&
        !url.search &&
        !url.hash &&
        url.port !== "0" &&
        !/[\\%\s\p{C}]/u.test(v.endpoint) &&
        !v.endpoint.split("/").some((segment) => segment === "." || segment === "..")
      );
    } catch {
      return false;
    }
  });

const nativePrivateText = z
  .string()
  .refine(
    (value) =>
      Boolean(value.trim()) &&
      new TextEncoder().encode(value).length <= 4000 &&
      !/[\p{C}\p{Z}]/u.test(value.replace(/[ \n\r\t]/g, ""))
  );
export const nativeWorkflowFormSchema = z.object({
  parametersText: z
    .string()
    .min(2)
    .refine((value) => new TextEncoder().encode(value).length <= 65536),
  timeoutSeconds: z.number().int().min(30).max(86400),
  acknowledgeLostNodeTeardown: z.boolean(),
});
export const nativeResetFormSchema = z
  .object({
    mode: z
      .string()
      .refine((value) =>
        ["retry_activity", "restart_rewrite", "resume_rewrite", "refresh_survivors"].includes(value)
      ),
    rationale: nativePrivateText,
    quiescenceEvidence: nativePrivateText,
    acknowledgeBoundaryReviewed: z.boolean().refine((value) => value),
    acknowledgeConflictingWorkStopped: z.boolean().refine((value) => value),
    retirementWorkflowId: z
      .string()
      .refine(
        (value) =>
          value === "" ||
          (Boolean(value.trim()) &&
            new TextEncoder().encode(value).length <= 1000 &&
            !/[\p{C}\p{Z}]/u.test(value.replace(/ /g, "")))
      ),
    timeoutSeconds: z.number().int().min(30).max(86400),
  })
  .refine(
    (value) => value.mode !== "refresh_survivors" || Boolean(value.retirementWorkflowId.trim())
  );
