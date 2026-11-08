import type { RequestNvkeProviderObservationInput } from "@/graphql/__generated__/operations";
import { NvkeAdmissionError } from "./admission";
import {
  archiveRecovery,
  archiveRejectedRecovery,
  nativeTextDigest,
  readRecovery,
  saveRecovery,
  validProviderRequestSource,
  withRecoveryLock,
  NvkeRecoveryError,
  type NvkeBrowserScope,
  type NvkeRecoveryOperation,
  type NvkeRecoveryRecord,
  type NvkeProviderRequestSource,
  type RecoveryStorage,
} from "./recovery";

export async function admitNvkeProviders<T extends NvkeRecoveryOperation>(options: {
  scope: NvkeBrowserScope;
  siteId: string;
  expectedSiteVersion: number;
  source: NvkeProviderRequestSource;
  revision: string;
  timeoutSeconds: number;
  storage: RecoveryStorage;
  current: () => boolean;
  sourceAvailable: () => boolean;
  onRecord: (record: NvkeRecoveryRecord | null) => void;
  receipt: (requestId: string) => Promise<T | null>;
  admit: (
    input: RequestNvkeProviderObservationInput
  ) => Promise<{ ok: boolean; data?: T | null; errors?: { code: string }[] } | null | undefined>;
}): Promise<T | null> {
  const scope = { ...options.scope },
    source = { ...options.source };
  const { siteId, expectedSiteVersion, revision, timeoutSeconds, storage, current } = options;
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
    if (!validProviderRequestSource(source)) throw new NvkeAdmissionError("SOURCE_CHANGED");
    if (!Number.isSafeInteger(timeoutSeconds) || timeoutSeconds < 30 || timeoutSeconds > 86400)
      throw new NvkeAdmissionError("REQUEST_REJECTED");
    const digest = await nativeTextDigest(JSON.stringify({ timeoutSeconds }));
    if (!current()) return null;
    if (record) {
      if (
        record.format !== 6 ||
        record.mode !== "providers" ||
        record.operationKey !== "infrastructure.providers" ||
        record.parametersDigest !== digest ||
        record.expectedSiteVersion !== expectedSiteVersion ||
        record.revision !== revision ||
        record.timeoutSeconds !== timeoutSeconds ||
        record.source.connectionId !== source.connectionId ||
        record.source.connectionVersion !== source.connectionVersion
      )
        throw new NvkeRecoveryError("RECOVERY_MISMATCH");
    } else {
      if (!options.sourceAvailable()) throw new NvkeAdmissionError("SOURCE_CHANGED");
      record = {
        ...scope,
        format: 6,
        mode: "providers",
        siteId,
        expectedSiteVersion,
        source,
        revision,
        timeoutSeconds,
        operationKey: "infrastructure.providers",
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
      connectionId: frozen.source.connectionId,
      expectedConnectionVersion: frozen.source.connectionVersion,
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
