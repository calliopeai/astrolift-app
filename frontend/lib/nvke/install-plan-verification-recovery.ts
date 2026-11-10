import { z } from "zod";
import type { NvkeInstallPlanContextJournalSource as ParentSource } from "./install-plan-context-recovery";
import { installPlanContextSelectionSchema } from "./operator-install-plan-context";
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
  contextId: guid,
  contextVersion: version,
  contextDigest: digest,
  item: installPlanContextSelectionSchema.shape.item,
  ordinal: installPlanContextSelectionSchema.shape.ordinal,
};
const sourceSchema = z
  .object(sourceShape)
  .strict()
  .refine(
    (row) =>
      new Set([row.operationId, row.planId, row.chartArtifactId, row.treeId, row.contextId])
        .size === 5
  );
const selectionSchema = z.object({ artifactId: guid }).strict();
const selectorSchema = selectionSchema.extend({ format: z.literal(1) }).strict();
const schema = z
  .object({
    format: z.literal(1),
    kind: z.literal("nvke-install-plan-verification"),
    ...sourceShape,
    ...selectionSchema.shape,
    inputSha256: digest,
  })
  .strict();
export type NvkeInstallPlanVerificationJournalSource = ParentSource & {
  contextId: string;
  contextVersion: number;
  contextDigest: string;
  item: string;
  ordinal: number;
};
export type NvkeInstallPlanVerificationChoice = z.infer<typeof selectionSchema>;
export type NvkeInstallPlanVerificationJournal = z.infer<typeof schema>;
export const installPlanVerificationRecoveryKey = (
  source: NvkeInstallPlanVerificationJournalSource
) =>
  "astrolift.nvke.install-plan-verification.v1:" +
  JSON.stringify([
    source.server,
    source.organizationId,
    source.actorId,
    source.operationId,
    source.planId,
    source.chartArtifactId,
    source.treeId,
    source.contextId,
  ]);
const intentKey = (source: NvkeInstallPlanVerificationJournalSource, artifactId: string) =>
  installPlanVerificationRecoveryKey(source) + ":intent:" + artifactId;

function safely<T>(run: () => T): T {
  try {
    return run();
  } catch (error) {
    if (error instanceof NvkeRecoveryError) throw error;
    throw new NvkeRecoveryError("STORAGE_UNAVAILABLE");
  }
}
function choice(source: NvkeInstallPlanVerificationJournalSource, value: unknown) {
  sourceSchema.parse(source);
  const selected = selectionSchema.parse(value);
  if (
    new Set([
      selected.artifactId,
      source.operationId,
      source.planId,
      source.chartArtifactId,
      source.treeId,
      source.contextId,
    ]).size !== 6
  )
    throw new NvkeRecoveryError("INVALID_RECOVERY");
  return selected;
}
export function readSelectedInstallPlanVerification(
  storage: RecoveryStorage,
  source: NvkeInstallPlanVerificationJournalSource
): NvkeInstallPlanVerificationChoice | null {
  return safely(() => {
    sourceSchema.parse(source);
    const raw = storage.getItem(installPlanVerificationRecoveryKey(source));
    if (raw === null) return null;
    if (raw.length > 1024) throw new NvkeRecoveryError("INVALID_RECOVERY");
    const parsed = selectorSchema.parse(JSON.parse(raw));
    return choice(source, {
      artifactId: parsed.artifactId,
    });
  });
}
export function selectInstallPlanVerification(
  storage: RecoveryStorage,
  source: NvkeInstallPlanVerificationJournalSource,
  value: NvkeInstallPlanVerificationChoice
) {
  safely(() => {
    const selected = choice(source, value),
      key = installPlanVerificationRecoveryKey(source);
    const text = JSON.stringify({ format: 1, ...selected });
    storage.setItem(key, text);
    if (storage.getItem(key) !== text) throw new NvkeRecoveryError("STORAGE_UNAVAILABLE");
  });
}
export function readInstallPlanVerificationJournal(
  storage: RecoveryStorage,
  source: NvkeInstallPlanVerificationJournalSource,
  value: NvkeInstallPlanVerificationChoice
): NvkeInstallPlanVerificationJournal | null {
  return safely(() => {
    const expected = sourceSchema.parse(source),
      selected = choice(source, value);
    const raw = storage.getItem(intentKey(source, selected.artifactId));
    if (raw === null) return null;
    if (raw.length > 16384) throw new NvkeRecoveryError("INVALID_RECOVERY");
    const result = schema.parse(JSON.parse(raw));
    if (
      Object.entries({ ...expected, ...selected }).some(
        ([key, value]) => result[key as keyof NvkeInstallPlanVerificationJournal] !== value
      )
    )
      throw new NvkeRecoveryError("RECOVERY_MISMATCH");
    return result;
  });
}
export function saveInstallPlanVerificationJournal(
  storage: RecoveryStorage,
  source: NvkeInstallPlanVerificationJournalSource,
  value: NvkeInstallPlanVerificationChoice,
  inputSha256: string
) {
  return safely(() => {
    const selected = choice(source, value),
      record = schema.parse({
        format: 1,
        kind: "nvke-install-plan-verification",
        ...source,
        ...selected,
        inputSha256,
      });
    const previous = readInstallPlanVerificationJournal(storage, source, selected);
    if (previous && previous.inputSha256 !== inputSha256)
      throw new NvkeRecoveryError("RECOVERY_MISMATCH");
    if (!previous) storage.setItem(intentKey(source, selected.artifactId), JSON.stringify(record));
    if (
      JSON.stringify(readInstallPlanVerificationJournal(storage, source, selected)) !==
      JSON.stringify(record)
    )
      throw new NvkeRecoveryError("STORAGE_UNAVAILABLE");
    selectInstallPlanVerification(storage, source, selected);
    return record;
  });
}
export function clearSelectedInstallPlanVerification(
  storage: RecoveryStorage,
  source: NvkeInstallPlanVerificationJournalSource,
  expected: NvkeInstallPlanVerificationChoice
) {
  safely(() => {
    const selected = choice(source, expected);
    if (
      JSON.stringify(readSelectedInstallPlanVerification(storage, source)) !==
      JSON.stringify(selected)
    )
      throw new NvkeRecoveryError("RECOVERY_MISMATCH");
    storage.removeItem(installPlanVerificationRecoveryKey(source));
    if (readSelectedInstallPlanVerification(storage, source) !== null)
      throw new NvkeRecoveryError("STORAGE_UNAVAILABLE");
  });
}
