import { expect, it } from "vitest";
import { INSTALL_TREE_PROPS } from "@/components/screens/nvke/NvkeOperatorInstallPlanTreePanel.stories";
import {
  installPlanLocalImageRequest,
  validateInstallPlanLocalImage,
  type NvkeInstallPlanLocalImage,
  type NvkeInstallPlanLocalImageForm,
} from "./operator-install-plan-local-image";

const plan = INSTALL_TREE_PROPS.plan!,
  charts = INSTALL_TREE_PROPS.charts!,
  tree = INSTALL_TREE_PROPS.artifacts!;
const id = "2be02f88-dc9b-47fb-9662-96504efde439";
const form: NvkeInstallPlanLocalImageForm = {
  artifactId: id,
  reference: "private.example.test/owned@sha256:" + "4".repeat(64),
  maximumBytes: "9007199254740993",
  timeoutSeconds: "300",
};
const row: NvkeInstallPlanLocalImage = {
  id,
  version: 2,
  status: "prepared",
  sourceKind: "local",
  planId: plan.id,
  planVersion: plan.version,
  planDigest: plan.plan.planDigest,
  chartArtifactId: charts.id,
  chartArtifactVersion: charts.version,
  chartArtifactDigest: charts.artifactDigest,
  treeId: tree.id,
  treeVersion: tree.version,
  treeDigest: tree.treeDigest,
  operationId: plan.operationId,
  operationVersion: plan.operationVersion,
  siteId: plan.siteId,
  siteVersion: plan.siteVersion,
  authorityId: plan.authorityId,
  authorityVersion: plan.authorityVersion,
  artifactDigest: "1".repeat(64),
  sourceDigest: "2".repeat(64),
  captureDigest: "3".repeat(64),
  rootDigest: "sha256:" + "4".repeat(64),
  blobCount: 6,
  bytes: form.maximumBytes,
  observedAt: "2026-11-10T00:00:00+00:00",
  executionState: "requires-workload-platform-credential-phase-use-and-linked-admission",
  continuationAuthorized: false,
};

it("preserves private input and decimal precision while binding the reviewed sources", () => {
  expect(installPlanLocalImageRequest(form, plan, charts, tree)).toEqual({
    format: 1,
    sourceKind: "local",
    artifactId: id,
    planId: plan.id,
    planDigest: plan.plan.planDigest,
    treeId: tree.id,
    treeDigest: tree.treeDigest,
    reference: form.reference,
    maximumBytes: form.maximumBytes,
    timeoutSeconds: 300,
  });
  expect(validateInstallPlanLocalImage(row, plan, charts, tree, id, form)).toEqual(row);
  expect(
    validateInstallPlanLocalImage(
      { ...row, rootDigest: "sha512:" + "5".repeat(128) },
      plan,
      charts,
      tree,
      id
    ).rootDigest
  ).toMatch(/^sha512:/);
});

it.each([
  { artifactId: plan.id },
  { artifactId: charts.id },
  { artifactId: tree.id },
  { artifactId: plan.operationId },
  { reference: "" },
  { reference: "https://private.example.test/a" },
  { reference: "private/a?secret" },
  { reference: "private/a#fragment" },
  { reference: "private\\a" },
  { reference: "private/a\n" },
  { reference: "private/é" },
  { policy: "reviewed\n" },
  { maximumBytes: "512\n" },
  { policy: "../policy" },
  { policy: "" },
  { maximumBytes: "0" },
  { maximumBytes: "01" },
  { maximumBytes: "9223372036854775808" },
  { maximumBytes: 9007199254740993 },
  { maximumBytes: null },
  { maximumCalls: "4097" },
  { maximumCalls: "0" },
  { maximumCalls: true },
  { timeoutSeconds: "301" },
  { timeoutSeconds: "0" },
  { credentials: "PRIVATE" },
  { destination: "/tmp/private" },
])("rejects invalid private request fields: %j", (change) => {
  expect(() => installPlanLocalImageRequest({ ...form, ...change }, plan, charts, tree)).toThrow();
});

it.each([
  { id: plan.id },
  { version: 0 },
  { status: "ready" },
  { planId: id },
  { planVersion: 99 },
  { planDigest: "0".repeat(64) },
  { chartArtifactId: id },
  { chartArtifactVersion: 99 },
  { chartArtifactDigest: "f".repeat(64) },
  { treeId: id },
  { treeVersion: 99 },
  { treeDigest: "f".repeat(64) },
  { operationId: id },
  { operationVersion: 99 },
  { siteId: id },
  { siteVersion: 99 },
  { authorityId: id },
  { authorityVersion: 99 },
  { artifactDigest: null },
  { sourceDigest: "PRIVATE" },
  { artifactDigest: "1".repeat(64) + "\n" },
  { rootDigest: row.rootDigest + "\n" },
  { captureDigest: null },
  { rootDigest: "sha256:short" },
  { blobCount: 0 },
  { blobCount: 100001 },
  { bytes: "0" },
  { bytes: "9223372036854775808" },
  { bytes: 42 },
  { observedAt: "2026-11-10T00:00:00" },
  { executionState: "ready" },
  { continuationAuthorized: true },
  { reference: "PRIVATE" },
])("rejects unbound, truncated or authorizing image metadata: %j", (change) => {
  expect(() =>
    validateInstallPlanLocalImage({ ...row, ...change }, plan, charts, tree, id)
  ).toThrow();
});

it("rejects stale parents and byte-limit violations", () => {
  expect(() =>
    installPlanLocalImageRequest(form, { ...plan, version: plan.version + 1 }, charts, tree)
  ).toThrow();
  expect(() =>
    installPlanLocalImageRequest(form, plan, { ...charts, version: charts.version + 1 }, tree)
  ).toThrow();
  expect(() =>
    validateInstallPlanLocalImage(row, plan, charts, { ...tree, version: tree.version + 1 }, id)
  ).toThrow();
  expect(() =>
    validateInstallPlanLocalImage(row, plan, charts, tree, id, {
      ...form,
      maximumBytes: "9007199254740992",
    })
  ).toThrow();
  expect(() =>
    validateInstallPlanLocalImage(row, plan, charts, tree, id, { ...form, maximumBytes: "PRIVATE" })
  ).toThrow();
});

it("requires explicit local kind and the exact selected image digest", () => {
  for (const sourceKind of [undefined, "registry", "LOCAL"]) {
    expect(() =>
      validateInstallPlanLocalImage({ ...row, sourceKind }, plan, charts, tree, id)
    ).toThrow();
  }
  expect(() =>
    validateInstallPlanLocalImage(
      { ...row, rootDigest: "sha256:" + "5".repeat(64) },
      plan,
      charts,
      tree,
      id,
      form
    )
  ).toThrow();
  expect(() =>
    installPlanLocalImageRequest(
      { ...form, reference: "private.example.test/owned:v1" },
      plan,
      charts,
      tree
    )
  ).toThrow();
  expect(() =>
    installPlanLocalImageRequest(
      { ...form, reference: "private@example.test/owned@sha256:" + "4".repeat(64) },
      plan,
      charts,
      tree
    )
  ).toThrow();
});
