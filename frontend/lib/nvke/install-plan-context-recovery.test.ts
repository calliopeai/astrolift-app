import { expect, it } from "vitest";
import { INSTALL_CONTEXT_PROPS } from "@/components/screens/nvke/NvkeOperatorInstallPlanContextPanel.stories";
import {
  readSelectedInstallPlanContext,
  readInstallPlanContextJournal,
  saveInstallPlanContextJournal,
  selectInstallPlanContext,
  clearSelectedInstallPlanContext,
  installPlanContextRecoveryKey,
  type NvkeInstallPlanContextJournalSource,
} from "./install-plan-context-recovery";
const f = INSTALL_CONTEXT_PROPS,
  plan = f.plan!,
  charts = f.charts!,
  tree = f.tree!,
  row = f.artifacts!;
const source: NvkeInstallPlanContextJournalSource = {
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
};
const chosen = { artifactId: row.id, item: row.item, ordinal: row.ordinal };
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
  const saved = saveInstallPlanContextJournal(s, source, chosen, digest);
  expect(readSelectedInstallPlanContext(s, source)).toEqual(chosen);
  expect(readInstallPlanContextJournal(s, source, chosen)).toEqual(saved);
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
  clearSelectedInstallPlanContext(s, source, chosen);
  expect(readSelectedInstallPlanContext(s, source)).toBeNull();
  expect(readInstallPlanContextJournal(s, source, chosen)).toEqual(saved);
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
])("never certifies another scope or reviewed source %#", (change) => {
  const s = storage();
  saveInstallPlanContextJournal(s, source, chosen, "e".repeat(64));
  const altered = { ...source, ...change };
  if (installPlanContextRecoveryKey(altered) !== installPlanContextRecoveryKey(source))
    expect(readInstallPlanContextJournal(s, altered, chosen)).toBeNull();
  else expect(() => readInstallPlanContextJournal(s, altered, chosen)).toThrow();
});
it.each([{ item: "controller" }, { ordinal: 0 }])(
  "cannot reuse an artifact for another item selection %#",
  (change) => {
    const s = storage();
    saveInstallPlanContextJournal(s, source, chosen, "e".repeat(64));
    expect(() => readInstallPlanContextJournal(s, source, { ...chosen, ...change })).toThrow();
    expect(() =>
      saveInstallPlanContextJournal(s, source, { ...chosen, ...change }, "e".repeat(64))
    ).toThrow();
  }
);
it("does not overwrite changed input or clear another tab's selection", () => {
  const s = storage();
  saveInstallPlanContextJournal(s, source, chosen, "e".repeat(64));
  expect(() => saveInstallPlanContextJournal(s, source, chosen, "f".repeat(64))).toThrow();
  expect(() => clearSelectedInstallPlanContext(s, source, { ...chosen, item: "other" })).toThrow();
  expect(readSelectedInstallPlanContext(s, source)).toEqual(chosen);
});
it.each([plan.id, charts.id, tree.id, plan.operationId])(
  "refuses a cross-kind artifact UUID %s",
  (artifactId) => {
    const s = storage();
    expect(() => selectInstallPlanContext(s, source, { ...chosen, artifactId })).toThrow();
    expect(s.items.size).toBe(0);
  }
);
it("detects silent writes, corrupt records and private extra fields", () => {
  const s = storage();
  expect(() =>
    saveInstallPlanContextJournal({ ...s, setItem: () => {} }, source, chosen, "e".repeat(64))
  ).toThrow();
  saveInstallPlanContextJournal(s, source, chosen, "e".repeat(64));
  const key = Array.from(s.items.keys()).find((key) => key.includes(":intent:"))!;
  const record = JSON.parse(s.items.get(key)!);
  s.items.set(key, JSON.stringify({ ...record, capture: "PRIVATE_BODY" }));
  expect(() => readInstallPlanContextJournal(s, source, chosen)).toThrow();
  s.items.set(
    installPlanContextRecoveryKey(source),
    JSON.stringify({ format: 1, ...chosen, credentials: "PRIVATE_BODY" })
  );
  expect(() => readSelectedInstallPlanContext(s, source)).toThrow();
});
