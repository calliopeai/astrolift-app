import { z } from "zod";
import type { NvkeInstallPlanSource } from "./operator-install-plan";
import {
  validNvkeGuid,
  NvkeRecoveryError,
  type NvkeBrowserScope,
  type RecoveryStorage,
} from "./recovery";
const guid = z.string().refine(validNvkeGuid);
const version = z.number().int().positive().safe();
const digest = z.string().regex(/^[0-9a-f]{64}$/);
const schema = z
  .object({
    format: z.literal(1),
    kind: z.literal("nvke-install-plan"),
    server: z.string().min(1).max(4096),
    organizationId: guid,
    actorId: version,
    siteId: guid,
    siteVersion: version,
    operationId: guid,
    operationVersion: version,
    authorityId: guid,
    authorityVersion: version,
    instanceId: guid,
    bundleManifestSha256: digest,
    nativeRevision: z.string().regex(/^[0-9a-f]{40}$/),
    planId: guid,
    inputSha256: digest,
  })
  .strict();
const sourceSchema = schema.omit({ format: true, kind: true, planId: true, inputSha256: true });
export type NvkeInstallPlanJournal = z.infer<typeof schema>;
export type NvkeInstallPlanJournalSource = NvkeBrowserScope & NvkeInstallPlanSource;
const prefix = "astrolift.nvke.install-plan.v1:";
export const installPlanRecoveryKey = (scope: NvkeBrowserScope, operationId: string) =>
  prefix + JSON.stringify([scope.server, scope.organizationId, scope.actorId, operationId]);
const intentKey = (scope: NvkeBrowserScope, operationId: string, planId: string) =>
  installPlanRecoveryKey(scope, operationId) + ":intent:" + planId;
function safely<T>(run: () => T): T {
  try {
    return run();
  } catch (error) {
    if (error instanceof NvkeRecoveryError) throw error;
    throw new NvkeRecoveryError("STORAGE_UNAVAILABLE");
  }
}
export function readSelectedInstallPlan(
  storage: RecoveryStorage,
  scope: NvkeBrowserScope,
  operationId: string
): string | null {
  return safely(() => {
    const raw = storage.getItem(installPlanRecoveryKey(scope, operationId));
    if (raw === null) return null;
    const selected =
      raw.length <= 1024
        ? z
            .object({ format: z.literal(1), planId: guid })
            .strict()
            .safeParse(JSON.parse(raw))
        : null;
    if (!selected?.success) throw new NvkeRecoveryError("INVALID_RECOVERY");
    return selected.data.planId;
  });
}
export function selectInstallPlan(
  storage: RecoveryStorage,
  scope: NvkeBrowserScope,
  operationId: string,
  planId: string
) {
  if (!validNvkeGuid(operationId) || !validNvkeGuid(planId) || planId === operationId)
    throw new NvkeRecoveryError("INVALID_RECOVERY");
  safely(() => {
    const key = installPlanRecoveryKey(scope, operationId),
      text = JSON.stringify({ format: 1, planId });
    storage.setItem(key, text);
    if (storage.getItem(key) !== text) throw new NvkeRecoveryError("STORAGE_UNAVAILABLE");
  });
}
export function readInstallPlanJournal(
  storage: RecoveryStorage,
  source: NvkeInstallPlanJournalSource,
  planId: string
): NvkeInstallPlanJournal | null {
  return safely(() => {
    const expected = sourceSchema.parse(source);
    const raw = storage.getItem(intentKey(source, source.operationId, planId));
    if (raw === null) return null;
    const result = raw.length <= 16384 ? schema.safeParse(JSON.parse(raw)) : null;
    if (!result?.success) throw new NvkeRecoveryError("INVALID_RECOVERY");
    const record = result.data;
    if (
      record.planId !== planId ||
      planId === source.operationId ||
      Object.entries(expected).some(
        ([key, value]) => record[key as keyof NvkeInstallPlanJournal] !== value
      )
    )
      throw new NvkeRecoveryError("RECOVERY_MISMATCH");
    return record;
  });
}
export function saveInstallPlanJournal(
  storage: RecoveryStorage,
  source: NvkeInstallPlanJournalSource,
  planId: string,
  inputSha256: string
) {
  const record = schema.parse({
    format: 1,
    kind: "nvke-install-plan",
    ...source,
    planId,
    inputSha256,
  });
  if (planId === source.operationId) throw new NvkeRecoveryError("INVALID_RECOVERY");
  safely(() => {
    const previous = readInstallPlanJournal(storage, source, planId);
    if (previous && previous.inputSha256 !== inputSha256)
      throw new NvkeRecoveryError("RECOVERY_MISMATCH");
    if (!previous)
      storage.setItem(intentKey(source, source.operationId, planId), JSON.stringify(record));
    if (JSON.stringify(readInstallPlanJournal(storage, source, planId)) !== JSON.stringify(record))
      throw new NvkeRecoveryError("STORAGE_UNAVAILABLE");
    selectInstallPlan(storage, source, source.operationId, planId);
  });
  return record;
}
export function clearSelectedInstallPlan(
  storage: RecoveryStorage,
  scope: NvkeBrowserScope,
  operationId: string,
  expected: string
) {
  safely(() => {
    if (readSelectedInstallPlan(storage, scope, operationId) !== expected)
      throw new NvkeRecoveryError("RECOVERY_MISMATCH");
    storage.removeItem(installPlanRecoveryKey(scope, operationId));
    if (readSelectedInstallPlan(storage, scope, operationId) !== null)
      throw new NvkeRecoveryError("STORAGE_UNAVAILABLE");
  });
}
export async function withInstallPlanLock<T>(
  scope: NvkeBrowserScope,
  operationId: string,
  run: () => Promise<T>
) {
  if (typeof navigator === "undefined" || !navigator.locks?.request)
    throw new NvkeRecoveryError("STORAGE_UNAVAILABLE");
  return navigator.locks.request(
    installPlanRecoveryKey(scope, operationId),
    { mode: "exclusive" },
    run
  );
}
