import type {
  RequestNvkeNativeWorkflowInput,
  RequestNvkeNativeResetInput,
} from "@/graphql/__generated__/operations";
import {
  archiveRecovery,
  archiveRejectedRecovery,
  nativeTextDigest,
  readRecovery,
  saveRecovery,
  withRecoveryLock,
  sameResourceSource,
  NvkeRecoveryError,
  type NvkeBrowserScope,
  type NvkeRecoveryOperation,
  type NvkeRecoveryRecord,
  type NvkeResourceSource,
  type RecoveryStorage,
} from "./recovery";
import { NvkeAdmissionError } from "./admission";

export type NvkeNativeWorkflow =
  | "RenewK8sCertsV2"
  | "RotateEtcdEncryptionKey"
  | "RetireLostEtcdRotationNode";
export interface NvkeResetReview {
  workflowId: string;
  baseRunId: string;
  workflowTaskEventId: string;
  activityEventId: string;
  activityType: string;
  phase: string;
  mode: string;
  rationale: string;
  quiescenceEvidence: string;
  acknowledgeBoundaryReviewed: boolean;
  acknowledgeConflictingWorkStopped: boolean;
  retirementWorkflowId: string;
}
export type NvkeNativeValues = { timeoutSeconds: number } & (
  | {
      mode: "workflow";
      workflow: NvkeNativeWorkflow;
      parametersText: string;
      acknowledgeLostNodeTeardown: boolean;
    }
  | { mode: "reset"; review: NvkeResetReview }
);
export interface NvkeNativeAuthorityReview {
  siteId: string;
  expectedSiteVersion: number;
  expectedActorId: number;
  expectedAuthorityVersion: number;
  sourceOperationId: string;
  expectedSourceVersion: number;
  requestId: string;
}
export type NvkeNativeAdmissionInput = NvkeNativeAuthorityReview &
  (
    | {
        mode: "workflow";
        workflow: NvkeNativeWorkflow;
        parametersText: string;
        acknowledgeLostNodeTeardown: boolean;
        timeoutSeconds: number;
      }
    | {
        mode: "reset";
        workflow: "RotateEtcdEncryptionKey";
        review: NvkeResetReview;
        timeoutSeconds: number;
      }
  );
export const nativeOperationKey = (values: NvkeNativeValues) =>
  values.mode === "reset"
    ? "temporal.reset.RotateEtcdEncryptionKey"
    : `temporal.${values.workflow}`;

function ordered(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(ordered);
  if (value && typeof value === "object")
    return Object.fromEntries(
      Object.entries(value)
        .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
        .map(([key, item]) => [key, ordered(item)])
    );
  return value;
}

export async function admitNvkeNative<T extends NvkeRecoveryOperation>(options: {
  scope: NvkeBrowserScope;
  siteId: string;
  expectedSiteVersion: number;
  authorityVersion: number;
  source: NvkeResourceSource;
  revision: string;
  values: NvkeNativeValues;
  storage: RecoveryStorage;
  current: () => boolean;
  sourceAvailable: () => boolean;
  onRecord: (record: NvkeRecoveryRecord | null) => void;
  receipt: (requestId: string) => Promise<T | null>;
  admit: (
    input: NvkeNativeAdmissionInput
  ) => Promise<{ ok: boolean; data?: T | null; errors?: { code: string }[] } | null | undefined>;
}): Promise<T | null> {
  // Freeze review text before awaiting another tab's journal lock.
  const values = structuredClone(options.values),
    source = { ...options.source },
    scope = { ...options.scope };
  const { siteId, expectedSiteVersion, authorityVersion, revision, storage, current } = options;
  return withRecoveryLock(scope, siteId, async () => {
    if (!current()) return null;
    let record = readRecovery(storage, scope, siteId);
    const fresh = record === null;
    if (record) {
      const operation = await options.receipt(record.requestId);
      if (!current()) return null;
      if (operation) {
        archiveRecovery(storage, record, operation);
        options.onRecord(null);
        return operation;
      }
    }
    const digest = await nativeTextDigest(JSON.stringify(ordered(values)));
    if (!current()) return null;
    const key = nativeOperationKey(values);
    if (record) {
      if (
        record.format !== 4 ||
        record.mode !== values.mode ||
        record.operationKey !== key ||
        record.parametersDigest !== digest ||
        record.timeoutSeconds !== values.timeoutSeconds ||
        record.expectedSiteVersion !== expectedSiteVersion ||
        record.authorityVersion !== authorityVersion ||
        record.revision !== revision ||
        !sameResourceSource(record.source, source)
      )
        throw new NvkeRecoveryError("RECOVERY_MISMATCH");
    } else {
      if (!options.sourceAvailable()) throw new NvkeAdmissionError("SOURCE_CHANGED");
      record = {
        ...scope,
        format: 4,
        mode: values.mode,
        siteId,
        expectedSiteVersion,
        authorityVersion,
        source,
        revision,
        operationKey: key,
        timeoutSeconds: values.timeoutSeconds,
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
    const common: NvkeNativeAuthorityReview = {
      siteId,
      expectedSiteVersion: frozen.expectedSiteVersion,
      expectedActorId: scope.actorId,
      expectedAuthorityVersion: frozen.authorityVersion,
      sourceOperationId: frozen.source.operationId,
      expectedSourceVersion: frozen.source.version,
      requestId: frozen.requestId,
    };
    const reply = await options.admit(
      values.mode === "workflow"
        ? { ...common, ...values }
        : { ...common, ...values, workflow: "RotateEtcdEncryptionKey" }
    );
    if (!current()) return null;
    if (!reply?.ok || !reply.data || reply.errors?.length) {
      const code = reply?.errors?.length === 1 ? reply.errors[0].code : "";
      // Invalid input precedes native idempotency lookup, so only a newly issued UUID proves rejection.
      if (
        reply?.ok === false &&
        !reply.data &&
        (code !== "INVALID_INPUT" || fresh) &&
        archiveRejectedRecovery(storage, frozen, code, fresh ? "native_first_reply" : "standard")
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

export async function recoverNvkeNative<T extends NvkeRecoveryOperation>(options: {
  scope: NvkeBrowserScope;
  siteId: string;
  storage: RecoveryStorage;
  current: () => boolean;
  receipt: (requestId: string) => Promise<T | null>;
  onRecord: (record: NvkeRecoveryRecord | null) => void;
}): Promise<T | null> {
  const scope = { ...options.scope },
    { siteId, storage, current } = options;
  return withRecoveryLock(scope, siteId, async () => {
    if (!current()) return null;
    const record = readRecovery(storage, scope, siteId);
    if (!record) return null;
    const row = await options.receipt(record.requestId);
    if (!current()) return null;
    if (!row) throw new NvkeAdmissionError("REQUEST_FAILED");
    archiveRecovery(storage, record, row);
    options.onRecord(null);
    return row;
  });
}

export function nativeMutationInput(
  value: NvkeNativeAdmissionInput
):
  | { kind: "workflow"; input: RequestNvkeNativeWorkflowInput }
  | { kind: "reset"; input: RequestNvkeNativeResetInput } {
  if (value.mode === "workflow") {
    return {
      kind: "workflow",
      input: {
        siteId: value.siteId,
        expectedSiteVersion: value.expectedSiteVersion,
        expectedActorId: value.expectedActorId,
        expectedAuthorityVersion: value.expectedAuthorityVersion,
        sourceOperationId: value.sourceOperationId,
        expectedSourceVersion: value.expectedSourceVersion,
        requestId: value.requestId,
        workflow: value.workflow,
        parametersText: value.parametersText,
        acknowledgeLostNodeTeardown: value.acknowledgeLostNodeTeardown,
        timeoutSeconds: value.timeoutSeconds,
      },
    };
  }
  return {
    kind: "reset",
    input: {
      siteId: value.siteId,
      expectedSiteVersion: value.expectedSiteVersion,
      expectedActorId: value.expectedActorId,
      expectedAuthorityVersion: value.expectedAuthorityVersion,
      sourceOperationId: value.sourceOperationId,
      expectedSourceVersion: value.expectedSourceVersion,
      requestId: value.requestId,
      workflow: "RotateEtcdEncryptionKey",
      timeoutSeconds: value.timeoutSeconds,
      workflowId: value.review.workflowId,
      baseRunId: value.review.baseRunId,
      workflowTaskEventId: value.review.workflowTaskEventId,
      activityEventId: value.review.activityEventId,
      activityType: value.review.activityType,
      phase: value.review.phase,
      mode: value.review.mode,
      rationale: value.review.rationale,
      quiescenceEvidence: value.review.quiescenceEvidence,
      acknowledgeBoundaryReviewed: value.review.acknowledgeBoundaryReviewed,
      acknowledgeConflictingWorkStopped: value.review.acknowledgeConflictingWorkStopped,
      retirementWorkflowId: value.review.retirementWorkflowId,
    },
  };
}
