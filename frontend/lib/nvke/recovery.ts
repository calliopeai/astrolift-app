/** Browser recovery metadata only. Native parameters and credentials never enter storage. */
export interface NvkeBrowserScope {
  server: string;
  organizationId: string;
  actorId: number;
}
interface RecoveryMetadata extends NvkeBrowserScope {
  siteId: string;
  expectedSiteVersion: number;
  operationKey: string;
  revision: string;
  timeoutSeconds: number;
  requestId: string;
  parametersDigest: string;
  operationId: string | null;
}
export type NvkeRecoveryRecord = RecoveryMetadata &
  (
    | { format: 1 }
    | { format: 2; mode: "native" | "inventory" }
    | { format: 3; mode: "resource"; source: NvkeResourceSource }
    | {
        format: 4;
        mode: "workflow" | "reset";
        source: NvkeResourceSource;
        authorityVersion: number;
      }
  );
export interface NvkeResourceSource {
  operationId: string;
  version: number;
  resourceKey: string | null;
}
export const validResourceSource = (value: unknown): value is NvkeResourceSource => {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const row = value as Record<string, unknown>;
  return (
    Object.keys(row).length === 3 &&
    validNvkeGuid(row.operationId) &&
    Number.isSafeInteger(row.version) &&
    (row.version as number) > 0 &&
    (row.resourceKey === null ||
      (typeof row.resourceKey === "string" &&
        row.resourceKey.length > 0 &&
        row.resourceKey.length <= 4096))
  );
};
export const resourceSourceMetadata = (value: unknown): NvkeResourceSource | null => {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const row = value as Record<string, unknown>;
  if (
    Object.keys(row).some(
      (key) => !["operationId", "version", "resourceKey", "__typename"].includes(key)
    ) ||
    (row.__typename !== undefined && row.__typename !== "NvkeResourceSourceType")
  )
    return null;
  const source = {
    operationId: row.operationId,
    version: row.version,
    resourceKey: row.resourceKey,
  };
  return validResourceSource(source) ? source : null;
};
export const sameResourceSource = (left: unknown, right: unknown) => {
  const first = resourceSourceMetadata(left),
    second = resourceSourceMetadata(right);
  return (
    !!first &&
    !!second &&
    first.operationId === second.operationId &&
    first.version === second.version &&
    first.resourceKey === second.resourceKey
  );
};
export const recoveryMode = (record: NvkeRecoveryRecord) =>
  record.format === 1 ? "native" : record.mode;
export interface NvkeRecoveryOperation {
  id: string;
  siteId: string;
  requestId: string;
  operationKey: string;
  isInventory: boolean;
  resourceSource?: unknown;
}
export interface RecoveryStorage {
  getItem: (key: string) => string | null;
  setItem: (key: string, value: string) => void;
  removeItem: (key: string) => void;
}
const PREFIX = "astrolift.nvke.recovery.v1:";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const emptyUUID = "00000000-0000-0000-0000-000000000000";
export const validNvkeGuid = (value: unknown): value is string =>
  typeof value === "string" && UUID.test(value) && value !== emptyUUID;
export const nvkeScopeKey = (scope: NvkeBrowserScope) =>
  JSON.stringify([scope.server, scope.organizationId, scope.actorId]);
export const recoveryKey = (scope: NvkeBrowserScope, siteId: string) =>
  PREFIX + JSON.stringify([scope.server, scope.organizationId, scope.actorId, siteId]);
export class NvkeRecoveryError extends Error {
  constructor(readonly code: "STORAGE_UNAVAILABLE" | "INVALID_RECOVERY" | "RECOVERY_MISMATCH") {
    super(code);
  }
}
const fields = [
  "format",
  "server",
  "organizationId",
  "actorId",
  "siteId",
  "expectedSiteVersion",
  "operationKey",
  "revision",
  "timeoutSeconds",
  "requestId",
  "parametersDigest",
  "operationId",
];
export function validateRecovery(
  value: unknown,
  scope: NvkeBrowserScope,
  siteId: string
): NvkeRecoveryRecord {
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new NvkeRecoveryError("INVALID_RECOVERY");
  const row = value as Record<string, unknown>;
  const expectedFields =
    row.format === 4
      ? [...fields, "mode", "source", "authorityVersion"]
      : row.format === 3
        ? [...fields, "mode", "source"]
        : row.format === 2
          ? [...fields, "mode"]
          : fields;
  if (
    Object.keys(row).length !== expectedFields.length ||
    Object.keys(row).some((key) => !expectedFields.includes(key)) ||
    (row.format !== 1 && row.format !== 2 && row.format !== 3 && row.format !== 4) ||
    (row.format === 4 &&
      ((row.mode !== "workflow" && row.mode !== "reset") ||
        !validResourceSource(row.source) ||
        row.source.resourceKey !== null ||
        !Number.isSafeInteger(row.authorityVersion) ||
        (row.authorityVersion as number) < 1 ||
        (row.mode === "reset"
          ? row.operationKey !== "temporal.reset.RotateEtcdEncryptionKey"
          : ![
              "temporal.RenewK8sCertsV2",
              "temporal.RotateEtcdEncryptionKey",
              "temporal.RetireLostEtcdRotationNode",
            ].includes(row.operationKey as string)))) ||
    (row.format === 3 && (row.mode !== "resource" || !validResourceSource(row.source))) ||
    (row.format === 2 && row.mode !== "native" && row.mode !== "inventory") ||
    row.server !== scope.server ||
    row.organizationId !== scope.organizationId ||
    row.actorId !== scope.actorId ||
    row.siteId !== siteId ||
    !validNvkeGuid(row.siteId) ||
    !validNvkeGuid(row.organizationId) ||
    !validNvkeGuid(row.requestId) ||
    (row.operationId !== null && !validNvkeGuid(row.operationId)) ||
    !Number.isSafeInteger(row.actorId) ||
    (row.actorId as number) < 1 ||
    !Number.isSafeInteger(row.expectedSiteVersion) ||
    (row.expectedSiteVersion as number) < 1 ||
    !Number.isSafeInteger(row.timeoutSeconds) ||
    (row.timeoutSeconds as number) < 30 ||
    (row.timeoutSeconds as number) > 86400 ||
    typeof row.revision !== "string" ||
    !/^[0-9a-f]{40}$/.test(row.revision) ||
    typeof row.parametersDigest !== "string" ||
    !/^[0-9a-f]{64}$/.test(row.parametersDigest) ||
    typeof row.operationKey !== "string" ||
    !/^[A-Za-z][A-Za-z0-9.]{0,127}$/.test(row.operationKey)
  )
    throw new NvkeRecoveryError("INVALID_RECOVERY");
  return row as unknown as NvkeRecoveryRecord;
}
export function readRecovery(
  storage: RecoveryStorage,
  scope: NvkeBrowserScope,
  siteId: string
): NvkeRecoveryRecord | null {
  try {
    const raw = storage.getItem(recoveryKey(scope, siteId));
    if (raw === null) return null;
    if (raw.length > 16384) throw new NvkeRecoveryError("INVALID_RECOVERY");
    return validateRecovery(JSON.parse(raw), scope, siteId);
  } catch (error) {
    if (error instanceof NvkeRecoveryError) throw error;
    throw new NvkeRecoveryError("STORAGE_UNAVAILABLE");
  }
}
export async function nativeTextDigest(text: string): Promise<string> {
  const result = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return Array.from(new Uint8Array(result), (byte) => byte.toString(16).padStart(2, "0")).join("");
}
export function saveRecovery(storage: RecoveryStorage, record: NvkeRecoveryRecord): void {
  validateRecovery(record, record, record.siteId);
  try {
    const key = recoveryKey(record, record.siteId),
      text = JSON.stringify(record);
    const current = storage.getItem(key);
    if (current !== null && current !== text) throw new NvkeRecoveryError("RECOVERY_MISMATCH");
    storage.setItem(key, text);
    if (storage.getItem(key) !== text) throw new NvkeRecoveryError("STORAGE_UNAVAILABLE");
  } catch (error) {
    if (error instanceof NvkeRecoveryError) throw error;
    throw new NvkeRecoveryError("STORAGE_UNAVAILABLE");
  }
}
export function assertOriginalRequest(
  record: NvkeRecoveryRecord,
  values: {
    operationKey: string;
    timeoutSeconds: number;
    mode?: "native" | "inventory" | "resource";
    source?: NvkeResourceSource;
  },
  digest: string
) {
  if (
    record.operationKey !== values.operationKey ||
    recoveryMode(record) !== (values.mode ?? "native") ||
    (record.format === 3 && !sameResourceSource(record.source, values.source)) ||
    record.timeoutSeconds !== values.timeoutSeconds ||
    record.parametersDigest !== digest
  )
    throw new NvkeRecoveryError("RECOVERY_MISMATCH");
}
export function archiveRecovery(
  storage: RecoveryStorage,
  record: NvkeRecoveryRecord,
  operation: NvkeRecoveryOperation
) {
  if (
    !validNvkeGuid(operation.id) ||
    record.siteId !== operation.siteId ||
    record.requestId !== operation.requestId ||
    typeof operation.isInventory !== "boolean" ||
    (record.format === 3
      ? !sameResourceSource(record.source, operation.resourceSource)
      : operation.resourceSource != null) ||
    (recoveryMode(record) === "inventory") !== operation.isInventory ||
    record.operationKey !== operation.operationKey
  )
    throw new NvkeRecoveryError("RECOVERY_MISMATCH");
  try {
    const key = recoveryKey(record, record.siteId),
      receipt = { ...record, operationId: operation.id },
      text = JSON.stringify(receipt);
    const current = readRecovery(storage, record, record.siteId);
    if (JSON.stringify(current) !== JSON.stringify(record))
      throw new NvkeRecoveryError("RECOVERY_MISMATCH");
    const archive = key + ":receipt:" + record.requestId;
    const previous = storage.getItem(archive);
    if (previous !== null && previous !== text) throw new NvkeRecoveryError("RECOVERY_MISMATCH");
    storage.setItem(archive, text);
    if (storage.getItem(archive) !== text) throw new NvkeRecoveryError("STORAGE_UNAVAILABLE");
    storage.removeItem(key);
    if (storage.getItem(key) !== null) throw new NvkeRecoveryError("STORAGE_UNAVAILABLE");
  } catch (error) {
    if (error instanceof NvkeRecoveryError) throw error;
    throw new NvkeRecoveryError("STORAGE_UNAVAILABLE");
  }
}
export async function withRecoveryLock<T>(
  scope: NvkeBrowserScope,
  siteId: string,
  run: () => Promise<T>
): Promise<T> {
  if (typeof navigator === "undefined" || !navigator.locks?.request)
    throw new NvkeRecoveryError("STORAGE_UNAVAILABLE");
  return navigator.locks.request(recoveryKey(scope, siteId), { mode: "exclusive" }, run);
}

/** Standard codes follow the receipt lookup. Additional native refusals are
 * definitive only for the first reply to a newly issued UUID. Authority/source
 * changes, conflicts and unknown outcomes retain their original identity. */
export function archiveRejectedRecovery(
  storage: RecoveryStorage,
  record: NvkeRecoveryRecord,
  code: string,
  policy: "standard" | "native_first_reply" = "standard"
): boolean {
  const allowed =
    ["INVALID_INPUT", "VERSION_MISMATCH", "SURFACE_UNAVAILABLE"].includes(code) ||
    (policy === "native_first_reply" &&
      record.format === 4 &&
      ["PRECONDITION", "RESOURCE_BUSY", "UNSUPPORTED"].includes(code));
  if (!allowed) return false;
  try {
    const key = recoveryKey(record, record.siteId);
    if (JSON.stringify(readRecovery(storage, record, record.siteId)) !== JSON.stringify(record))
      throw new NvkeRecoveryError("RECOVERY_MISMATCH");
    const archive = key + ":rejected:" + record.requestId;
    const text = JSON.stringify({ ...record, rejectionCode: code });
    const previous = storage.getItem(archive);
    if (previous !== null && previous !== text) throw new NvkeRecoveryError("RECOVERY_MISMATCH");
    storage.setItem(archive, text);
    if (storage.getItem(archive) !== text) throw new NvkeRecoveryError("STORAGE_UNAVAILABLE");
    storage.removeItem(key);
    if (storage.getItem(key) !== null) throw new NvkeRecoveryError("STORAGE_UNAVAILABLE");
    return true;
  } catch (error) {
    if (error instanceof NvkeRecoveryError) throw error;
    throw new NvkeRecoveryError("STORAGE_UNAVAILABLE");
  }
}
