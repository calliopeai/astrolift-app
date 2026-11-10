import { expect, it } from "vitest";
import { INSTALL_VERIFICATION_PROPS } from "@/components/screens/nvke/NvkeOperatorInstallPlanVerificationPanel.stories";
import {
  readSelectedInstallPlanVerification,
  readInstallPlanVerificationJournal,
  saveInstallPlanVerificationJournal,
  selectInstallPlanVerification,
  clearSelectedInstallPlanVerification,
  installPlanVerificationRecoveryKey,
  type NvkeInstallPlanVerificationJournalSource,
} from "./install-plan-verification-recovery";
const f = INSTALL_VERIFICATION_PROPS,
  plan = f.plan!,
  charts = f.charts!,
  tree = f.tree!,
  row = f.artifacts!,
  captured = f.context!;
const source: NvkeInstallPlanVerificationJournalSource = {
  server: "https://astro.test/app/gql/config/",
  organizationId: plan.siteId,
  actorId: 41,
  siteId: plan.siteId,
  siteVersion: plan.siteVersion,
  operationId: plan.operationId,
  operationVersion: plan.operationVersion,
  authorityId: plan.authorityId,
  authorityVersion: plan.authorityVersion,
  instanceId: plan.plan.source.instanceId,
  bundleManifestSha256: plan.plan.source.bundleManifestSha256,
  nativeRevision: plan.plan.source.nativeRevision,
  planId: plan.id,
  planVersion: plan.version,
  planDigest: plan.plan.planDigest,
  chartArtifactId: charts.id,
  chartArtifactVersion: charts.version,
  chartArtifactDigest: charts.artifactDigest,
  treeId: tree.id,
  treeVersion: tree.version,
  treeDigest: tree.treeDigest,
  contextId: captured.id,
  contextVersion: captured.version,
  contextDigest: captured.contextDigest,
  item: captured.item,
  ordinal: captured.ordinal,
};
const chosen = { artifactId: row.id };
function storage() {
  const items = new Map<string, string>();
  return {
    items,
    getItem: (key: string) => items.get(key) ?? null,
    setItem: (key: string, value: string) => {
      items.set(key, value);
    },
    removeItem: (key: string) => {
      items.delete(key);
    },
  };
}
it("retains only source metadata, phase selection and exact-input hash across reloads", () => {
  const s = storage(),
    digest = "e".repeat(64);
  const saved = saveInstallPlanVerificationJournal(s, source, chosen, digest);
  expect(readSelectedInstallPlanVerification(s, source)).toEqual(chosen);
  expect(readInstallPlanVerificationJournal(s, source, chosen)).toEqual(saved);
  expect(saved.inputSha256).toBe(digest);
  const raw = Array.from(s.items.values()).join();
  for (const field of [
    "requestText",
    "maximumBytes",
    "revision",
    "upgrade",
    "rendered",
    "credentials",
    "lookupContent",
  ])
    expect(raw).not.toContain(field);
  clearSelectedInstallPlanVerification(s, source, chosen);
  expect(readSelectedInstallPlanVerification(s, source)).toBeNull();
  expect(readInstallPlanVerificationJournal(s, source, chosen)).toEqual(saved);
});
it.each([
  { actorId: 42 },
  { server: "https://other.test" },
  { organizationId: tree.id },
  { siteVersion: source.siteVersion + 1 },
  { authorityVersion: source.authorityVersion + 1 },
  { planVersion: source.planVersion + 1 },
  { chartArtifactVersion: source.chartArtifactVersion + 1 },
  { treeVersion: source.treeVersion + 1 },
  { treeDigest: "f".repeat(64) },
  { contextVersion: captured.version + 1 },
  { contextDigest: "f".repeat(64) },
  { contextId: "7d98e41e-ff9c-4290-bc01-9fce74037cdc" },
])("never certifies another scope or reviewed source %#", (change) => {
  const s = storage();
  saveInstallPlanVerificationJournal(s, source, chosen, "e".repeat(64));
  const altered = { ...source, ...change };
  if (installPlanVerificationRecoveryKey(altered) !== installPlanVerificationRecoveryKey(source))
    expect(readInstallPlanVerificationJournal(s, altered, chosen)).toBeNull();
  else expect(() => readInstallPlanVerificationJournal(s, altered, chosen)).toThrow();
});
it.each([{ item: "controller" }, { ordinal: 0 }])(
  "cannot reuse an artifact for another item selection %#",
  (change) => {
    const s = storage();
    saveInstallPlanVerificationJournal(s, source, chosen, "e".repeat(64));
    expect(() => readInstallPlanVerificationJournal(s, { ...source, ...change }, chosen)).toThrow();
    expect(() =>
      saveInstallPlanVerificationJournal(s, { ...source, ...change }, chosen, "e".repeat(64))
    ).toThrow();
  }
);
it("does not overwrite changed input or clear another tab's selection", () => {
  const s = storage();
  saveInstallPlanVerificationJournal(s, source, chosen, "e".repeat(64));
  expect(() => saveInstallPlanVerificationJournal(s, source, chosen, "f".repeat(64))).toThrow();
  expect(() =>
    clearSelectedInstallPlanVerification(s, source, {
      artifactId: "8d98e41e-ff9c-4290-bc01-9fce74037cdc",
    })
  ).toThrow();
  expect(readSelectedInstallPlanVerification(s, source)).toEqual(chosen);
});
it.each([plan.id, charts.id, tree.id, captured.id, plan.operationId])(
  "refuses a cross-kind artifact UUID %s",
  (artifactId) => {
    const s = storage();
    expect(() => selectInstallPlanVerification(s, source, { ...chosen, artifactId })).toThrow();
    expect(s.items.size).toBe(0);
  }
);
it("detects silent writes, corrupt records and private extra fields", () => {
  const s = storage();
  expect(() =>
    saveInstallPlanVerificationJournal({ ...s, setItem: () => {} }, source, chosen, "e".repeat(64))
  ).toThrow();
  saveInstallPlanVerificationJournal(s, source, chosen, "e".repeat(64));
  const key = Array.from(s.items.keys()).find((key) => key.includes(":intent:"))!;
  const record = JSON.parse(s.items.get(key)!);
  s.items.set(key, JSON.stringify({ ...record, capture: "PRIVATE_BODY" }));
  expect(() => readInstallPlanVerificationJournal(s, source, chosen)).toThrow();
  s.items.set(
    installPlanVerificationRecoveryKey(source),
    JSON.stringify({ format: 1, ...chosen, credentials: "PRIVATE_BODY" })
  );
  expect(() => readSelectedInstallPlanVerification(s, source)).toThrow();
});
