import {
  archiveRecovery,
  archiveRejectedRecovery,
  assertOriginalRequest,
  nativeTextDigest,
  readRecovery,
  saveRecovery,
  withRecoveryLock,
  type NvkeBrowserScope,
  type NvkeRecoveryOperation,
  type NvkeRecoveryRecord,
  type RecoveryStorage,
} from "./recovery";

export class NvkeAdmissionError extends Error {
  constructor(readonly code: "REQUEST_FAILED" | "REQUEST_REJECTED" | "SOURCE_CHANGED") {
    super(code);
  }
}

export interface NvkeAdmissionInput {
  siteId: string;
  expectedSiteVersion: number;
  expectedActorId: number;
  requestId: string;
  operationKey: string;
  timeoutSeconds: number;
  parametersText: string;
}

/** Both editors share one journal and lock so changing views cannot replace an unknown request. */
export async function admitNvkeRequest<T extends NvkeRecoveryOperation>(options: {
  scope: NvkeBrowserScope;
  siteId: string;
  expectedSiteVersion: number;
  revision: string;
  mode: "native" | "inventory";
  values: { operationKey: string; timeoutSeconds: number; parametersText: string };
  storage: RecoveryStorage;
  current: () => boolean;
  sourceAvailable: () => boolean;
  onRecord: (record: NvkeRecoveryRecord | null) => void;
  receipt: (requestId: string) => Promise<T | null>;
  admit: (input: NvkeAdmissionInput) => Promise<
    | {
        ok: boolean;
        data?: T | null;
        errors: { code: string }[];
      }
    | null
    | undefined
  >;
}): Promise<T | null> {
  const { scope, siteId, values, storage, current } = options;
  return withRecoveryLock(scope, siteId, async () => {
    if (!current()) return null;
    let original = readRecovery(storage, scope, siteId);
    if (original) {
      const operation = await options.receipt(original.requestId);
      if (!current()) return null;
      if (operation) {
        archiveRecovery(storage, original, operation);
        options.onRecord(null);
        return operation;
      }
    }
    const digest = await nativeTextDigest(values.parametersText);
    if (!current()) return null;
    if (original) assertOriginalRequest(original, { ...values, mode: options.mode }, digest);
    else {
      if (!options.sourceAvailable()) throw new NvkeAdmissionError("SOURCE_CHANGED");
      original = {
        ...scope,
        format: 2,
        mode: options.mode,
        siteId,
        expectedSiteVersion: options.expectedSiteVersion,
        revision: options.revision,
        operationKey: values.operationKey,
        timeoutSeconds: values.timeoutSeconds,
        requestId: crypto.randomUUID(),
        parametersDigest: digest,
        operationId: null,
      };
      saveRecovery(storage, original);
    }
    options.onRecord(original);
    const reply = await options.admit({
      siteId,
      expectedSiteVersion: original.expectedSiteVersion,
      expectedActorId: scope.actorId,
      requestId: original.requestId,
      operationKey: original.operationKey,
      timeoutSeconds: original.timeoutSeconds,
      parametersText: values.parametersText,
    });
    if (!current()) return null;
    if (!reply?.ok || !reply.data) {
      if (
        reply?.ok === false &&
        !reply.data &&
        reply.errors.length === 1 &&
        archiveRejectedRecovery(storage, original, reply.errors[0].code)
      ) {
        options.onRecord(null);
        throw new NvkeAdmissionError("REQUEST_REJECTED");
      }
      throw new NvkeAdmissionError("REQUEST_FAILED");
    }
    archiveRecovery(storage, original, reply.data);
    options.onRecord(null);
    return reply.data;
  });
}
