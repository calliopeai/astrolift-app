import { expect, it } from "vitest";
import { INSTALL_CONTEXT_PROPS } from "@/components/screens/nvke/NvkeOperatorInstallPlanContextPanel.stories";
import {
  readSelectedInstallPlanWorkload,
  readInstallPlanWorkloadJournal,
  saveInstallPlanWorkloadJournal,
  selectInstallPlanWorkload,
  clearSelectedInstallPlanWorkload,
  installPlanWorkloadRecoveryKey,
  type NvkeInstallPlanWorkloadJournalSource,
} from "./install-plan-workload-recovery";
const f = INSTALL_CONTEXT_PROPS,
  plan = f.plan!,
  charts = f.charts!,
  tree = f.tree!;
const source: NvkeInstallPlanWorkloadJournalSource = {
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
const chosen = { artifactId: "2be02f88-dc9b-47fb-9662-96504efde439" };
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
it("retains only source metadata, artifact selection and exact-input hash across reloads", () => {
  const s = storage(),
    digest = "e".repeat(64);
  const saved = saveInstallPlanWorkloadJournal(s, source, chosen, digest);
  expect(readSelectedInstallPlanWorkload(s, source)).toEqual(chosen);
  expect(readInstallPlanWorkloadJournal(s, source, chosen)).toEqual(saved);
  expect(saved.inputSha256).toBe(digest);
  const raw = Array.from(s.items.values()).join();
  for (const field of [
    "requestText",
    "maximumBytes",
    "contextBindings",
    "maximumResources",
    "reference",
    "policy",
    "rendered",
    "credentials",
    "lookupContent",
  ])
    expect(raw).not.toContain(field);
  clearSelectedInstallPlanWorkload(s, source, chosen);
  expect(readSelectedInstallPlanWorkload(s, source)).toBeNull();
  expect(readInstallPlanWorkloadJournal(s, source, chosen)).toEqual(saved);
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
  saveInstallPlanWorkloadJournal(s, source, chosen, "e".repeat(64));
  const altered = { ...source, ...change };
  if (installPlanWorkloadRecoveryKey(altered) !== installPlanWorkloadRecoveryKey(source))
    expect(readInstallPlanWorkloadJournal(s, altered, chosen)).toBeNull();
  else expect(() => readInstallPlanWorkloadJournal(s, altered, chosen)).toThrow();
});
it.each([{ item: "controller" }, { ordinal: 0 }])(
  "rejects item/ordinal fields outside the workload selection %#",
  (change) => {
    const s = storage();
    saveInstallPlanWorkloadJournal(s, source, chosen, "e".repeat(64));
    expect(() => readInstallPlanWorkloadJournal(s, source, { ...chosen, ...change })).toThrow();
    expect(() =>
      saveInstallPlanWorkloadJournal(s, source, { ...chosen, ...change }, "e".repeat(64))
    ).toThrow();
  }
);
it("does not overwrite changed input or clear another tab's selection", () => {
  const s = storage();
  saveInstallPlanWorkloadJournal(s, source, chosen, "e".repeat(64));
  expect(() => saveInstallPlanWorkloadJournal(s, source, chosen, "f".repeat(64))).toThrow();
  expect(() =>
    clearSelectedInstallPlanWorkload(s, source, {
      ...chosen,
      artifactId: "c5f2545b-31d6-4e81-9628-261ebee18431",
    })
  ).toThrow();
  expect(readSelectedInstallPlanWorkload(s, source)).toEqual(chosen);
});
it.each([plan.id, charts.id, tree.id, plan.operationId])(
  "refuses a cross-kind artifact UUID %s",
  (artifactId) => {
    const s = storage();
    expect(() => selectInstallPlanWorkload(s, source, { ...chosen, artifactId })).toThrow();
    expect(s.items.size).toBe(0);
  }
);
it("detects silent writes, corrupt records and private extra fields", () => {
  const s = storage();
  expect(() =>
    saveInstallPlanWorkloadJournal({ ...s, setItem: () => {} }, source, chosen, "e".repeat(64))
  ).toThrow();
  saveInstallPlanWorkloadJournal(s, source, chosen, "e".repeat(64));
  const key = Array.from(s.items.keys()).find((key) => key.includes(":intent:"))!;
  const record = JSON.parse(s.items.get(key)!);
  s.items.set(key, JSON.stringify({ ...record, capture: "PRIVATE_BODY" }));
  expect(() => readInstallPlanWorkloadJournal(s, source, chosen)).toThrow();
  s.items.set(
    installPlanWorkloadRecoveryKey(source),
    JSON.stringify({ format: 1, ...chosen, credentials: "PRIVATE_BODY" })
  );
  expect(() => readSelectedInstallPlanWorkload(s, source)).toThrow();
});
