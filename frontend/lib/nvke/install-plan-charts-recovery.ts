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
    kind: z.literal("nvke-install-plan-charts"),
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
    planVersion: version,
    planDigest: digest,
    artifactId: guid,
    inputSha256: digest,
  })
  .strict();
const sourceSchema = schema.omit({ format: true, kind: true, artifactId: true, inputSha256: true });
export type NvkeInstallPlanChartsJournal = z.infer<typeof schema>;
export type NvkeInstallPlanChartsJournalSource = NvkeBrowserScope &
  NvkeInstallPlanSource & { planId: string; planVersion: number; planDigest: string };
const prefix = "astrolift.nvke.install-plan-charts.v1:";
export const installPlanChartsRecoveryKey = (
  scope: NvkeBrowserScope,
  operationId: string,
  parentPlanId: string
) =>
  prefix +
  JSON.stringify([scope.server, scope.organizationId, scope.actorId, operationId, parentPlanId]);
const intentKey = (source: NvkeInstallPlanChartsJournalSource, artifactId: string) =>
  installPlanChartsRecoveryKey(source, source.operationId, source.planId) + ":intent:" + artifactId;
function safely<T>(run: () => T): T {
  try {
    return run();
  } catch (error) {
    if (error instanceof NvkeRecoveryError) throw error;
    throw new NvkeRecoveryError("STORAGE_UNAVAILABLE");
  }
}
export function readSelectedInstallPlanCharts(
  storage: RecoveryStorage,
  scope: NvkeBrowserScope,
  operationId: string,
  parentPlanId: string
): string | null {
  return safely(() => {
    const raw = storage.getItem(installPlanChartsRecoveryKey(scope, operationId, parentPlanId));
    if (raw === null) return null;
    const selected =
      raw.length <= 1024
        ? z
            .object({ format: z.literal(1), artifactId: guid })
            .strict()
            .safeParse(JSON.parse(raw))
        : null;
    if (!selected?.success) throw new NvkeRecoveryError("INVALID_RECOVERY");
    return selected.data.artifactId;
  });
}
export function selectInstallPlanCharts(
  storage: RecoveryStorage,
  scope: NvkeBrowserScope,
  operationId: string,
  parentPlanId: string,
  artifactId: string
) {
  if (
    !validNvkeGuid(operationId) ||
    !validNvkeGuid(parentPlanId) ||
    !validNvkeGuid(artifactId) ||
    artifactId === parentPlanId ||
    artifactId === operationId
  )
    throw new NvkeRecoveryError("INVALID_RECOVERY");
  safely(() => {
    const key = installPlanChartsRecoveryKey(scope, operationId, parentPlanId),
      text = JSON.stringify({ format: 1, artifactId });
    storage.setItem(key, text);
    if (storage.getItem(key) !== text) throw new NvkeRecoveryError("STORAGE_UNAVAILABLE");
  });
}
export function readInstallPlanChartsJournal(
  storage: RecoveryStorage,
  source: NvkeInstallPlanChartsJournalSource,
  artifactId: string
): NvkeInstallPlanChartsJournal | null {
  return safely(() => {
    const expected = sourceSchema.parse(source);
    const raw = storage.getItem(intentKey(source, artifactId));
    if (raw === null) return null;
    const result = raw.length <= 16384 ? schema.safeParse(JSON.parse(raw)) : null;
    if (!result?.success) throw new NvkeRecoveryError("INVALID_RECOVERY");
    const record = result.data;
    if (
      record.artifactId !== artifactId ||
      artifactId === source.operationId ||
      artifactId === source.planId ||
      Object.entries(expected).some(
        ([key, value]) => record[key as keyof NvkeInstallPlanChartsJournal] !== value
      )
    )
      throw new NvkeRecoveryError("RECOVERY_MISMATCH");
    return record;
  });
}
export function saveInstallPlanChartsJournal(
  storage: RecoveryStorage,
  source: NvkeInstallPlanChartsJournalSource,
  artifactId: string,
  inputSha256: string
) {
  const record = schema.parse({
    format: 1,
    kind: "nvke-install-plan-charts",
    ...source,
    artifactId,
    inputSha256,
  });
  if (artifactId === source.operationId || artifactId === source.planId)
    throw new NvkeRecoveryError("INVALID_RECOVERY");
  safely(() => {
    const previous = readInstallPlanChartsJournal(storage, source, artifactId);
    if (previous && previous.inputSha256 !== inputSha256)
      throw new NvkeRecoveryError("RECOVERY_MISMATCH");
    if (!previous) storage.setItem(intentKey(source, artifactId), JSON.stringify(record));
    if (
      JSON.stringify(readInstallPlanChartsJournal(storage, source, artifactId)) !==
      JSON.stringify(record)
    )
      throw new NvkeRecoveryError("STORAGE_UNAVAILABLE");
    selectInstallPlanCharts(storage, source, source.operationId, source.planId, artifactId);
  });
  return record;
}
export function clearSelectedInstallPlanCharts(
  storage: RecoveryStorage,
  scope: NvkeBrowserScope,
  operationId: string,
  parentPlanId: string,
  expected: string
) {
  safely(() => {
    if (readSelectedInstallPlanCharts(storage, scope, operationId, parentPlanId) !== expected)
      throw new NvkeRecoveryError("RECOVERY_MISMATCH");
    storage.removeItem(installPlanChartsRecoveryKey(scope, operationId, parentPlanId));
    if (readSelectedInstallPlanCharts(storage, scope, operationId, parentPlanId) !== null)
      throw new NvkeRecoveryError("STORAGE_UNAVAILABLE");
  });
}
