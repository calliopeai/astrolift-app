import { expect, it } from "vitest";
import { INSTALL_CONTEXT_PROPS } from "@/components/screens/nvke/NvkeOperatorInstallPlanContextPanel.stories";
import {
  readSelectedInstallPlanImmutable,
  readInstallPlanImmutableJournal,
  saveInstallPlanImmutableJournal,
  selectInstallPlanImmutable,
  clearSelectedInstallPlanImmutable,
  installPlanImmutableRecoveryKey,
  type NvkeInstallPlanImmutableJournalSource,
} from "./install-plan-immutable-recovery";
const f = INSTALL_CONTEXT_PROPS,
  plan = f.plan!,
  charts = f.charts!,
  tree = f.tree!;
const source: NvkeInstallPlanImmutableJournalSource = {
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
  workloadId: "c098fc8d-3f48-45fd-b9b0-7d670261e9ef",
  workloadVersion: 2,
  workloadDigest: "4".repeat(64),
  mode: "payload",
  bindingId: "61ba2d0d-ae12-4c09-9c0d-36bd92dd2dfd",
  bindingVersion: 2,
  bindingDigest: "5".repeat(64),
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
  const saved = saveInstallPlanImmutableJournal(s, source, chosen, digest);
  expect(readSelectedInstallPlanImmutable(s, source)).toEqual(chosen);
  expect(readInstallPlanImmutableJournal(s, source, chosen)).toEqual(saved);
  expect(saved.inputSha256).toBe(digest);
  const raw = Array.from(s.items.values()).join();
  for (const field of [
    "requestText",
    "maximumBytes",
    "payloadBindings",
    "item",
    "maximumResources",
    "maximumImageBytes",
    "reference",
    "policy",
    "rendered",
    "credentials",
    "lookupContent",
  ])
    expect(raw).not.toContain(field);
  clearSelectedInstallPlanImmutable(s, source, chosen);
  expect(readSelectedInstallPlanImmutable(s, source)).toBeNull();
  expect(readInstallPlanImmutableJournal(s, source, chosen)).toEqual(saved);
});
it.each([
  { actorId: 42 },
  { mode: "execution" as const },
  { bindingVersion: 3 },
  { bindingDigest: "f".repeat(64) },
  { bindingId: "c5f2545b-31d6-4e81-9628-261ebee18431" },
  { server: "https://other.test" },
  { organizationId: tree.id },
  { siteVersion: source.siteVersion + 1 },
  { authorityVersion: source.authorityVersion + 1 },
  { planVersion: source.planVersion + 1 },
  { chartArtifactVersion: source.chartArtifactVersion + 1 },
  { treeVersion: source.treeVersion + 1 },
  { treeDigest: "f".repeat(64) },
  { workloadVersion: 3 },
  { workloadDigest: "f".repeat(64) },
  { workloadId: "95654fc3-d84a-47e5-87a4-f1d3e1234567" },
])("never certifies another scope or reviewed source %#", (change) => {
  const s = storage();
  saveInstallPlanImmutableJournal(s, source, chosen, "e".repeat(64));
  const altered = { ...source, ...change };
  if (installPlanImmutableRecoveryKey(altered) !== installPlanImmutableRecoveryKey(source))
    expect(readInstallPlanImmutableJournal(s, altered, chosen)).toBeNull();
  else expect(() => readInstallPlanImmutableJournal(s, altered, chosen)).toThrow();
});
it.each([{ item: "controller" }, { ordinal: 0 }])(
  "rejects item/ordinal fields outside the workload selection %#",
  (change) => {
    const s = storage();
    saveInstallPlanImmutableJournal(s, source, chosen, "e".repeat(64));
    expect(() => readInstallPlanImmutableJournal(s, source, { ...chosen, ...change })).toThrow();
    expect(() =>
      saveInstallPlanImmutableJournal(s, source, { ...chosen, ...change }, "e".repeat(64))
    ).toThrow();
  }
);
it("does not overwrite changed input or clear another tab's selection", () => {
  const s = storage();
  saveInstallPlanImmutableJournal(s, source, chosen, "e".repeat(64));
  expect(() => saveInstallPlanImmutableJournal(s, source, chosen, "f".repeat(64))).toThrow();
  expect(() =>
    clearSelectedInstallPlanImmutable(s, source, {
      ...chosen,
      artifactId: "c5f2545b-31d6-4e81-9628-261ebee18431",
    })
  ).toThrow();
  expect(readSelectedInstallPlanImmutable(s, source)).toEqual(chosen);
});
it.each([plan.id, charts.id, tree.id, plan.operationId, source.workloadId, source.bindingId])(
  "refuses a cross-kind artifact UUID %s",
  (artifactId) => {
    const s = storage();
    expect(() => selectInstallPlanImmutable(s, source, { ...chosen, artifactId })).toThrow();
    expect(s.items.size).toBe(0);
  }
);
it("detects silent writes, corrupt records and private extra fields", () => {
  const s = storage();
  expect(() =>
    saveInstallPlanImmutableJournal({ ...s, setItem: () => {} }, source, chosen, "e".repeat(64))
  ).toThrow();
  saveInstallPlanImmutableJournal(s, source, chosen, "e".repeat(64));
  const key = Array.from(s.items.keys()).find((key) => key.includes(":intent:"))!;
  const record = JSON.parse(s.items.get(key)!);
  s.items.set(key, JSON.stringify({ ...record, capture: "PRIVATE_BODY" }));
  expect(() => readInstallPlanImmutableJournal(s, source, chosen)).toThrow();
  s.items.set(
    installPlanImmutableRecoveryKey(source),
    JSON.stringify({ format: 1, ...chosen, credentials: "PRIVATE_BODY" })
  );
  expect(() => readSelectedInstallPlanImmutable(s, source)).toThrow();
});

it("isolates artifact kinds and refuses changed kind or duplicate metadata", () => {
  const s = storage();
  const payload = saveInstallPlanImmutableJournal(s, source, chosen, "e".repeat(64));
  const execution = { ...source, mode: "execution" as const };
  expect(readSelectedInstallPlanImmutable(s, execution)).toBeNull();
  const tree = saveInstallPlanImmutableJournal(s, execution, chosen, "f".repeat(64));
  expect(tree.kind).toBe("nvke-install-plan-immutable-execution-tree");
  expect(payload.kind).toBe("nvke-install-plan-image-payload");
  expect(readInstallPlanImmutableJournal(s, source, chosen)).toEqual(payload);
  const key = Array.from(s.items.keys()).find(
    (k) => k.startsWith(installPlanImmutableRecoveryKey(source)) && k.includes(":intent:")
  )!;
  s.items.set(key, JSON.stringify({ ...payload, kind: tree.kind }));
  expect(() => readInstallPlanImmutableJournal(s, source, chosen)).toThrow();
  s.items.set(key, JSON.stringify(payload).replace('"format":1', '"format":1,"format":1'));
  expect(() => readInstallPlanImmutableJournal(s, source, chosen)).toThrow();
  s.items.set(
    installPlanImmutableRecoveryKey(source),
    JSON.stringify({ format: 1, ...chosen }).replace('"format":1', '"format":1,"\\u0066ormat":1')
  );
  expect(() => readSelectedInstallPlanImmutable(s, source)).toThrow();
});

it("isolates all four artifact kinds and rejects copied or private node platform records", () => {
  const s = storage();
  const sources = (["payload", "execution", "platform", "pull-authority"] as const).map((mode) => ({
    ...source,
    mode,
  }));
  const records = sources.map((scope, index) =>
    saveInstallPlanImmutableJournal(s, scope, chosen, String(index + 1).repeat(64))
  );
  expect(records.map((record) => record.kind)).toEqual([
    "nvke-install-plan-image-payload",
    "nvke-install-plan-immutable-execution-tree",
    "nvke-install-plan-node-platforms",
    "nvke-install-plan-pull-authority",
  ]);
  sources.forEach((scope, index) => {
    expect(readInstallPlanImmutableJournal(s, scope, chosen)).toEqual(records[index]);
  });
  const platform = sources[2];
  const key = installPlanImmutableRecoveryKey(platform) + ":intent:" + chosen.artifactId;
  for (const record of [
    records[0],
    records[1],
    { ...records[2], nodePlatforms: "PRIVATE_SCOPE" },
    { ...records[2], runtimeProfile: "containerd/v2.3.0" },
    { ...records[2], nodeUid: "PRIVATE_UID" },
  ]) {
    s.items.set(key, JSON.stringify(record));
    expect(() => readInstallPlanImmutableJournal(s, platform, chosen)).toThrow();
  }
  s.items.set(key, JSON.stringify(records[2]));
  expect(() =>
    readInstallPlanImmutableJournal(
      s,
      { ...platform, bindingVersion: platform.bindingVersion + 1 },
      chosen
    )
  ).toThrow();
  clearSelectedInstallPlanImmutable(s, platform, chosen);
  expect(readSelectedInstallPlanImmutable(s, platform)).toBeNull();
  expect(readInstallPlanImmutableJournal(s, platform, chosen)).toEqual(records[2]);
  expect(readSelectedInstallPlanImmutable(s, sources[0])).toEqual(chosen);
  expect(readSelectedInstallPlanImmutable(s, sources[1])).toEqual(chosen);
});

it("keeps pull observation journals private and separate from every other artifact kind", () => {
  const s = storage(),
    pull = { ...source, mode: "pull-authority" as const };
  const record = saveInstallPlanImmutableJournal(s, pull, chosen, "a".repeat(64));
  const key = installPlanImmutableRecoveryKey(pull) + ":intent:" + chosen.artifactId;
  for (const change of [
    { selectedPods: { "private/pod": "private-uid" } },
    { maximumCalls: 512 },
    { credentials: "PRIVATE" },
    { kind: "nvke-install-plan-node-platforms" },
    { mode: "platform" },
  ]) {
    s.items.set(key, JSON.stringify({ ...record, ...change }));
    expect(() => readInstallPlanImmutableJournal(s, pull, chosen)).toThrow();
  }
});

it("binds Secret interpretation recovery to the exact pull parent without changing legacy storage keys", () => {
  expect(installPlanImmutableRecoveryKey(source)).toBe(
    "astrolift.nvke.install-plan-immutable.v1:" +
      JSON.stringify([
        source.server,
        source.organizationId,
        source.actorId,
        source.operationId,
        source.planId,
        source.chartArtifactId,
        source.treeId,
        source.workloadId,
        source.bindingId,
        source.mode,
      ])
  );
  const selected = {
    ...source,
    mode: "pull-secret-analysis" as const,
    pullAuthorityId: "84cd76e5-12ab-46fe-a89c-abc123abcd78",
    pullAuthorityVersion: 2,
    pullAuthorityDigest: "7".repeat(64),
  };
  const s = storage();
  const saved = saveInstallPlanImmutableJournal(s, selected, chosen, "e".repeat(64));
  expect(saved.kind).toBe("nvke-install-plan-pull-secret-analysis");
  expect(readSelectedInstallPlanImmutable(s, selected)).toEqual(chosen);
  expect(readInstallPlanImmutableJournal(s, selected, chosen)).toEqual(saved);
  for (const change of [
    { pullAuthorityVersion: 3 },
    { pullAuthorityDigest: "8".repeat(64) },
    { pullAuthorityId: "85cd76e5-12ab-46fe-a89c-abc123abcd78" },
  ]) {
    expect(readSelectedInstallPlanImmutable(s, { ...selected, ...change })).toBeNull();
    expect(readInstallPlanImmutableJournal(s, { ...selected, ...change }, chosen)).toBeNull();
  }
  expect(() =>
    saveInstallPlanImmutableJournal(
      s,
      selected,
      { artifactId: selected.pullAuthorityId },
      "e".repeat(64)
    )
  ).toThrow();
  for (const invalid of [
    { ...source, mode: "pull-secret-analysis" as const },
    { ...selected, pullAuthorityId: source.bindingId },
    { ...selected, mode: "payload" as const },
  ])
    expect(() => saveInstallPlanImmutableJournal(s, invalid, chosen, "e".repeat(64))).toThrow();
});
