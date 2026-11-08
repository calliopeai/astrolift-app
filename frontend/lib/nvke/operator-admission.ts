import type { RequestNvkeOperatorJobInput } from "@/graphql/__generated__/operations";
import { NvkeAdmissionError } from "./admission";
import {
  archiveRecovery,
  archiveRejectedRecovery,
  nativeTextDigest,
  readRecovery,
  saveRecovery,
  withRecoveryLock,
  NvkeRecoveryError,
  type NvkeBrowserScope,
  type NvkeRecoveryOperation,
  type NvkeRecoveryRecord,
  type RecoveryStorage,
} from "./recovery";

export interface NvkeOperatorJobValues {
  operationKey: string;
  requestText: string;
  timeoutSeconds: number;
  acknowledgeInfrastructureChange: boolean;
}

export function operatorEnvelopeMatches(
  text: string,
  operationKey: string,
  siteId: string,
  revision: string
): boolean {
  try {
    const bytes = new TextEncoder().encode(text);
    if (bytes.length > 1048576 || new TextDecoder().decode(bytes) !== text) return false;
    const row = JSON.parse(text);
    return (
      !!row &&
      !Array.isArray(row) &&
      Object.keys(row).length === 7 &&
      Object.keys(row).every((key) =>
        ["format", "revision", "siteId", "command", "positionals", "options", "files"].includes(key)
      ) &&
      row.format === 1 &&
      row.siteId === siteId &&
      row.revision === revision &&
      /^tooling\.[a-z][a-z0-9.-]{0,119}$/.test(operationKey) &&
      operationKey === `tooling.${row.command}` &&
      Array.isArray(row.positionals) &&
      row.options !== null &&
      typeof row.options === "object" &&
      !Array.isArray(row.options) &&
      row.files !== null &&
      typeof row.files === "object" &&
      !Array.isArray(row.files) &&
      Object.keys(row.files).length <= 32 &&
      Object.values(row.files).every(
        (value) => typeof value === "string" && new TextEncoder().encode(value).length <= 524288
      )
    );
  } catch {
    return false;
  }
}

export async function admitNvkeOperatorJob<T extends NvkeRecoveryOperation>(options: {
  scope: NvkeBrowserScope;
  siteId: string;
  expectedSiteVersion: number;
  expectedAuthorityVersion: number;
  revision: string;
  values: NvkeOperatorJobValues;
  storage: RecoveryStorage;
  current: () => boolean;
  sourceAvailable: () => boolean;
  onRecord: (record: NvkeRecoveryRecord | null) => void;
  receipt: (requestId: string) => Promise<T | null>;
  admit: (
    input: RequestNvkeOperatorJobInput
  ) => Promise<{ ok: boolean; data?: T | null; errors?: { code: string }[] } | null | undefined>;
}): Promise<T | null> {
  const scope = { ...options.scope },
    values = { ...options.values };
  const { siteId, expectedSiteVersion, expectedAuthorityVersion, revision, storage, current } =
    options;
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
    if (
      !operatorEnvelopeMatches(values.requestText, values.operationKey, siteId, revision) ||
      !Number.isSafeInteger(values.timeoutSeconds) ||
      values.timeoutSeconds < 30 ||
      values.timeoutSeconds > 86400 ||
      typeof values.acknowledgeInfrastructureChange !== "boolean"
    )
      throw new NvkeAdmissionError("REQUEST_REJECTED");
    const digest = await nativeTextDigest(values.requestText);
    if (!current()) return null;
    if (record) {
      if (
        record.format !== 7 ||
        record.operationKey !== values.operationKey ||
        record.parametersDigest !== digest ||
        record.expectedSiteVersion !== expectedSiteVersion ||
        record.authorityVersion !== expectedAuthorityVersion ||
        record.revision !== revision ||
        record.timeoutSeconds !== values.timeoutSeconds ||
        record.acknowledgeInfrastructureChange !== values.acknowledgeInfrastructureChange
      )
        throw new NvkeRecoveryError("RECOVERY_MISMATCH");
    }
    if (!options.sourceAvailable()) throw new NvkeAdmissionError("SOURCE_CHANGED");
    if (!record) {
      record = {
        ...scope,
        format: 7,
        mode: "tooling",
        siteId,
        expectedSiteVersion,
        authorityVersion: expectedAuthorityVersion,
        revision,
        operationKey: values.operationKey,
        timeoutSeconds: values.timeoutSeconds,
        acknowledgeInfrastructureChange: values.acknowledgeInfrastructureChange,
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
    const reply = await options.admit({
      siteId,
      expectedActorId: scope.actorId,
      expectedSiteVersion: record.expectedSiteVersion,
      expectedAuthorityVersion: record.authorityVersion,
      requestId: record.requestId,
      requestText: values.requestText,
      timeoutSeconds: record.timeoutSeconds,
      acknowledgeInfrastructureChange: record.acknowledgeInfrastructureChange,
    });
    if (!current()) return null;
    if (!reply?.ok || !reply.data || reply.errors?.length) {
      // Only a definitive first reply can authorize a replacement UUID.
      if (
        fresh &&
        reply?.ok === false &&
        !reply.data &&
        reply.errors?.length === 1 &&
        archiveRejectedRecovery(storage, record, reply.errors[0].code)
      ) {
        options.onRecord(null);
        throw new NvkeAdmissionError("REQUEST_REJECTED");
      }
      throw new NvkeAdmissionError("REQUEST_FAILED");
    }
    archiveRecovery(storage, record, reply.data);
    options.onRecord(null);
    return reply.data;
  });
}
