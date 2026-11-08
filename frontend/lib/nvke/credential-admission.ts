import type { RequestNvkeCredentialObservationInput } from "@/graphql/__generated__/operations";
import { NvkeAdmissionError } from "./admission";
import {
  archiveRecovery,
  archiveRejectedRecovery,
  nativeTextDigest,
  readRecovery,
  saveRecovery,
  sameResourceSource,
  withRecoveryLock,
  NvkeRecoveryError,
  type NvkeBrowserScope,
  type NvkeRecoveryOperation,
  type NvkeRecoveryRecord,
  type NvkeResourceSource,
  type RecoveryStorage,
} from "./recovery";

export async function admitNvkeCredentials<T extends NvkeRecoveryOperation>(options: {
  scope: NvkeBrowserScope;
  siteId: string;
  expectedSiteVersion: number;
  authorityVersion: number;
  source: NvkeResourceSource;
  revision: string;
  timeoutSeconds: number;
  storage: RecoveryStorage;
  current: () => boolean;
  sourceAvailable: () => boolean;
  onRecord: (record: NvkeRecoveryRecord | null) => void;
  receipt: (requestId: string) => Promise<T | null>;
  admit: (
    input: RequestNvkeCredentialObservationInput
  ) => Promise<{ ok: boolean; data?: T | null; errors?: { code: string }[] } | null | undefined>;
}): Promise<T | null> {
  const scope = { ...options.scope },
    source = { ...options.source };
  const {
    siteId,
    expectedSiteVersion,
    authorityVersion,
    revision,
    timeoutSeconds,
    storage,
    current,
  } = options;
  return withRecoveryLock(scope, siteId, async () => {
    if (!current()) return null;
    let record = readRecovery(storage, scope, siteId);
    const fresh = record === null;
    if (record) {
      const row = await options.receipt(record.requestId);
      if (!current()) return null;
      if (row) {
        archiveRecovery(storage, record, row);
        options.onRecord(null);
        return row;
      }
    }
    if (!Number.isSafeInteger(timeoutSeconds) || timeoutSeconds < 30 || timeoutSeconds > 86400)
      throw new NvkeAdmissionError("REQUEST_REJECTED");
    const digest = await nativeTextDigest(JSON.stringify({ timeoutSeconds }));
    if (!current()) return null;
    if (record) {
      if (
        record.format !== 5 ||
        record.mode !== "credentials" ||
        record.operationKey !== "metrics.credentials" ||
        record.parametersDigest !== digest ||
        record.expectedSiteVersion !== expectedSiteVersion ||
        record.authorityVersion !== authorityVersion ||
        record.revision !== revision ||
        record.timeoutSeconds !== timeoutSeconds ||
        !sameResourceSource(record.source, source)
      )
        throw new NvkeRecoveryError("RECOVERY_MISMATCH");
    } else {
      if (!options.sourceAvailable()) throw new NvkeAdmissionError("SOURCE_CHANGED");
      record = {
        ...scope,
        format: 5,
        mode: "credentials",
        siteId,
        expectedSiteVersion,
        authorityVersion,
        source,
        revision,
        timeoutSeconds,
        operationKey: "metrics.credentials",
        requestId: crypto.randomUUID(),
        parametersDigest: digest,
        operationId: null,
      };
      saveRecovery(storage, record);
    }
    options.onRecord(record);
    if (!current()) return null;
    if (JSON.stringify(readRecovery(storage, scope, siteId)) !== JSON.stringify(record))
      throw new NvkeRecoveryError("RECOVERY_MISMATCH");
    const frozen = record;
    const reply = await options.admit({
      siteId,
      expectedActorId: scope.actorId,
      expectedSiteVersion: frozen.expectedSiteVersion,
      expectedAuthorityVersion: frozen.authorityVersion,
      sourceOperationId: frozen.source.operationId,
      expectedSourceVersion: frozen.source.version,
      requestId: frozen.requestId,
      timeoutSeconds: frozen.timeoutSeconds,
    });
    if (!current()) return null;
    if (!reply?.ok || !reply.data || reply.errors?.length) {
      const code = reply?.errors?.length === 1 ? reply.errors[0].code : "";
      if (
        reply?.ok === false &&
        !reply.data &&
        (code !== "INVALID_INPUT" || fresh) &&
        archiveRejectedRecovery(storage, frozen, code)
      ) {
        options.onRecord(null);
        throw new NvkeAdmissionError("REQUEST_REJECTED");
      }
      throw new NvkeAdmissionError("REQUEST_FAILED");
    }
    archiveRecovery(storage, frozen, reply.data);
    options.onRecord(null);
    return reply.data;
  });
}
