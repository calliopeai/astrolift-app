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
    kind: z.literal("nvke-install-plan-tree"),
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
    chartArtifactId: guid,
    chartArtifactVersion: version,
    chartArtifactDigest: digest,
    artifactId: guid,
    inputSha256: digest,
  })
  .strict();
const sourceSchema = schema.omit({ format: true, kind: true, artifactId: true, inputSha256: true });
export type NvkeInstallPlanTreeJournal = z.infer<typeof schema>;
export type NvkeInstallPlanTreeJournalSource = NvkeBrowserScope &
  NvkeInstallPlanSource & {
    planId: string;
    planVersion: number;
    planDigest: string;
    chartArtifactId: string;
    chartArtifactVersion: number;
    chartArtifactDigest: string;
  };
const prefix = "astrolift.nvke.install-plan-tree.v1:";
export const installPlanTreeRecoveryKey = (
  scope: NvkeBrowserScope,
  operationId: string,
  parentPlanId: string,
  chartArtifactId: string
) =>
  prefix +
  JSON.stringify([
    scope.server,
    scope.organizationId,
    scope.actorId,
    operationId,
    parentPlanId,
    chartArtifactId,
  ]);
const intentKey = (source: NvkeInstallPlanTreeJournalSource, artifactId: string) =>
  installPlanTreeRecoveryKey(source, source.operationId, source.planId, source.chartArtifactId) +
  ":intent:" +
  artifactId;
function safely<T>(run: () => T): T {
  try {
    return run();
  } catch (error) {
    if (error instanceof NvkeRecoveryError) throw error;
    throw new NvkeRecoveryError("STORAGE_UNAVAILABLE");
  }
}
export function readSelectedInstallPlanTree(
  storage: RecoveryStorage,
  scope: NvkeBrowserScope,
  operationId: string,
  parentPlanId: string,
  chartArtifactId: string
): string | null {
  return safely(() => {
    const raw = storage.getItem(
      installPlanTreeRecoveryKey(scope, operationId, parentPlanId, chartArtifactId)
    );
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
export function selectInstallPlanTree(
  storage: RecoveryStorage,
  scope: NvkeBrowserScope,
  operationId: string,
  parentPlanId: string,
  chartArtifactId: string,
  artifactId: string
) {
  if (
    !validNvkeGuid(operationId) ||
    !validNvkeGuid(parentPlanId) ||
    !validNvkeGuid(chartArtifactId) ||
    !validNvkeGuid(artifactId) ||
    artifactId === chartArtifactId ||
    artifactId === parentPlanId ||
    artifactId === operationId
  )
    throw new NvkeRecoveryError("INVALID_RECOVERY");
  safely(() => {
    const key = installPlanTreeRecoveryKey(scope, operationId, parentPlanId, chartArtifactId),
      text = JSON.stringify({ format: 1, artifactId });
    storage.setItem(key, text);
    if (storage.getItem(key) !== text) throw new NvkeRecoveryError("STORAGE_UNAVAILABLE");
  });
}
export function readInstallPlanTreeJournal(
  storage: RecoveryStorage,
  source: NvkeInstallPlanTreeJournalSource,
  artifactId: string
): NvkeInstallPlanTreeJournal | null {
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
      artifactId === source.chartArtifactId ||
      Object.entries(expected).some(
        ([key, value]) => record[key as keyof NvkeInstallPlanTreeJournal] !== value
      )
    )
      throw new NvkeRecoveryError("RECOVERY_MISMATCH");
    return record;
  });
}
export function saveInstallPlanTreeJournal(
  storage: RecoveryStorage,
  source: NvkeInstallPlanTreeJournalSource,
  artifactId: string,
  inputSha256: string
) {
  const record = schema.parse({
    format: 1,
    kind: "nvke-install-plan-tree",
    ...source,
    artifactId,
    inputSha256,
  });
  if (
    artifactId === source.operationId ||
    artifactId === source.planId ||
    artifactId === source.chartArtifactId
  )
    throw new NvkeRecoveryError("INVALID_RECOVERY");
  safely(() => {
    const previous = readInstallPlanTreeJournal(storage, source, artifactId);
    if (previous && previous.inputSha256 !== inputSha256)
      throw new NvkeRecoveryError("RECOVERY_MISMATCH");
    if (!previous) storage.setItem(intentKey(source, artifactId), JSON.stringify(record));
    if (
      JSON.stringify(readInstallPlanTreeJournal(storage, source, artifactId)) !==
      JSON.stringify(record)
    )
      throw new NvkeRecoveryError("STORAGE_UNAVAILABLE");
    selectInstallPlanTree(
      storage,
      source,
      source.operationId,
      source.planId,
      source.chartArtifactId,
      artifactId
    );
  });
  return record;
}
export function clearSelectedInstallPlanTree(
  storage: RecoveryStorage,
  scope: NvkeBrowserScope,
  operationId: string,
  parentPlanId: string,
  chartArtifactId: string,
  expected: string
) {
  safely(() => {
    if (
      readSelectedInstallPlanTree(storage, scope, operationId, parentPlanId, chartArtifactId) !==
      expected
    )
      throw new NvkeRecoveryError("RECOVERY_MISMATCH");
    storage.removeItem(
      installPlanTreeRecoveryKey(scope, operationId, parentPlanId, chartArtifactId)
    );
    if (
      readSelectedInstallPlanTree(storage, scope, operationId, parentPlanId, chartArtifactId) !==
      null
    )
      throw new NvkeRecoveryError("STORAGE_UNAVAILABLE");
  });
}
