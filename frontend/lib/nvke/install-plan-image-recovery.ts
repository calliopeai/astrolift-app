import { z } from "zod";
import type { NvkeInstallPlanTreeJournalSource } from "./install-plan-tree-recovery";
import { validNvkeGuid, NvkeRecoveryError, type RecoveryStorage } from "./recovery";

const guid = z.string().refine(validNvkeGuid),
  version = z.number().int().positive().safe();
const digest = z
  .string()
  .length(64)
  .regex(/^[0-9a-f]{64}$/);
const sourceShape = {
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
  nativeRevision: z
    .string()
    .length(40)
    .regex(/^[0-9a-f]{40}$/),
  planId: guid,
  planVersion: version,
  planDigest: digest,
  chartArtifactId: guid,
  chartArtifactVersion: version,
  chartArtifactDigest: digest,
  treeId: guid,
  treeVersion: version,
  treeDigest: digest,
};
const sourceSchema = z
  .object(sourceShape)
  .strict()
  .refine(
    (row) => new Set([row.operationId, row.planId, row.chartArtifactId, row.treeId]).size === 4
  );
const selectionSchema = z.object({ artifactId: guid }).strict();
const selectorSchema = selectionSchema.extend({ format: z.literal(1) }).strict();
const schema = z
  .object({
    format: z.literal(1),
    kind: z.literal("nvke-install-plan-image"),
    ...sourceShape,
    ...selectionSchema.shape,
    inputSha256: digest,
  })
  .strict();
export type NvkeInstallPlanImageJournalSource = NvkeInstallPlanTreeJournalSource & {
  treeId: string;
  treeVersion: number;
  treeDigest: string;
};
export type NvkeInstallPlanImageChoice = z.infer<typeof selectionSchema>;
export type NvkeInstallPlanImageJournal = z.infer<typeof schema>;
export const installPlanImageRecoveryKey = (source: NvkeInstallPlanImageJournalSource) =>
  "astrolift.nvke.install-plan-image.v1:" +
  JSON.stringify([
    source.server,
    source.organizationId,
    source.actorId,
    source.operationId,
    source.planId,
    source.chartArtifactId,
    source.treeId,
  ]);
const intentKey = (source: NvkeInstallPlanImageJournalSource, artifactId: string) =>
  installPlanImageRecoveryKey(source) + ":intent:" + artifactId;

function safely<T>(run: () => T): T {
  try {
    return run();
  } catch (error) {
    if (error instanceof NvkeRecoveryError) throw error;
    throw new NvkeRecoveryError("STORAGE_UNAVAILABLE");
  }
}
function choice(source: NvkeInstallPlanImageJournalSource, value: unknown) {
  sourceSchema.parse(source);
  const selected = selectionSchema.parse(value);
  if (
    new Set([
      selected.artifactId,
      source.operationId,
      source.planId,
      source.chartArtifactId,
      source.treeId,
    ]).size !== 5
  )
    throw new NvkeRecoveryError("INVALID_RECOVERY");
  return selected;
}
export function readSelectedInstallPlanImage(
  storage: RecoveryStorage,
  source: NvkeInstallPlanImageJournalSource
): NvkeInstallPlanImageChoice | null {
  return safely(() => {
    sourceSchema.parse(source);
    const raw = storage.getItem(installPlanImageRecoveryKey(source));
    if (raw === null) return null;
    if (raw.length > 1024) throw new NvkeRecoveryError("INVALID_RECOVERY");
    const parsed = selectorSchema.parse(JSON.parse(raw));
    return choice(source, {
      artifactId: parsed.artifactId,
    });
  });
}
export function selectInstallPlanImage(
  storage: RecoveryStorage,
  source: NvkeInstallPlanImageJournalSource,
  value: NvkeInstallPlanImageChoice
) {
  safely(() => {
    const selected = choice(source, value),
      key = installPlanImageRecoveryKey(source);
    const text = JSON.stringify({ format: 1, ...selected });
    storage.setItem(key, text);
    if (storage.getItem(key) !== text) throw new NvkeRecoveryError("STORAGE_UNAVAILABLE");
  });
}
export function readInstallPlanImageJournal(
  storage: RecoveryStorage,
  source: NvkeInstallPlanImageJournalSource,
  value: NvkeInstallPlanImageChoice
): NvkeInstallPlanImageJournal | null {
  return safely(() => {
    const expected = sourceSchema.parse(source),
      selected = choice(source, value);
    const raw = storage.getItem(intentKey(source, selected.artifactId));
    if (raw === null) return null;
    if (raw.length > 16384) throw new NvkeRecoveryError("INVALID_RECOVERY");
    const result = schema.parse(JSON.parse(raw));
    if (
      Object.entries({ ...expected, ...selected }).some(
        ([key, value]) => result[key as keyof NvkeInstallPlanImageJournal] !== value
      )
    )
      throw new NvkeRecoveryError("RECOVERY_MISMATCH");
    return result;
  });
}
export function saveInstallPlanImageJournal(
  storage: RecoveryStorage,
  source: NvkeInstallPlanImageJournalSource,
  value: NvkeInstallPlanImageChoice,
  inputSha256: string
) {
  return safely(() => {
    const selected = choice(source, value),
      record = schema.parse({
        format: 1,
        kind: "nvke-install-plan-image",
        ...source,
        ...selected,
        inputSha256,
      });
    const previous = readInstallPlanImageJournal(storage, source, selected);
    if (previous && previous.inputSha256 !== inputSha256)
      throw new NvkeRecoveryError("RECOVERY_MISMATCH");
    if (!previous) storage.setItem(intentKey(source, selected.artifactId), JSON.stringify(record));
    if (
      JSON.stringify(readInstallPlanImageJournal(storage, source, selected)) !==
      JSON.stringify(record)
    )
      throw new NvkeRecoveryError("STORAGE_UNAVAILABLE");
    selectInstallPlanImage(storage, source, selected);
    return record;
  });
}
export function clearSelectedInstallPlanImage(
  storage: RecoveryStorage,
  source: NvkeInstallPlanImageJournalSource,
  expected: NvkeInstallPlanImageChoice
) {
  safely(() => {
    const selected = choice(source, expected);
    if (JSON.stringify(readSelectedInstallPlanImage(storage, source)) !== JSON.stringify(selected))
      throw new NvkeRecoveryError("RECOVERY_MISMATCH");
    storage.removeItem(installPlanImageRecoveryKey(source));
    if (readSelectedInstallPlanImage(storage, source) !== null)
      throw new NvkeRecoveryError("STORAGE_UNAVAILABLE");
  });
}
