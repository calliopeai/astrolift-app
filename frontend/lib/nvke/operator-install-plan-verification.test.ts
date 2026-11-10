import { expect, it } from "vitest";
import { INSTALL_CONTEXT_PROPS } from "@/components/screens/nvke/NvkeOperatorInstallPlanContextPanel.stories";
import {
  installPlanVerificationRequest,
  validateInstallPlanVerification,
} from "./operator-install-plan-verification";

const plan = INSTALL_CONTEXT_PROPS.plan!,
  charts = INSTALL_CONTEXT_PROPS.charts!,
  tree = INSTALL_CONTEXT_PROPS.tree!,
  captured = INSTALL_CONTEXT_PROPS.artifacts!;
const id = "690ad665-6588-43da-9614-09e01fbc4a01";
const form = {
  artifactId: id,
  maximumBytes: "16777216",
  maximumCaptureAgeSeconds: "3600",
  maximumCalls: "512",
  timeoutSeconds: "300",
};
function reply(): Record<string, unknown> {
  const row: Record<string, unknown> = {
    ...captured,
    __typename: "NvkeOperatorInstallPlanVerificationType",
    id,
    contextId: captured.id,
    contextVersion: captured.version,
    artifactDigest: "4".repeat(64),
    verificationDigest: "5".repeat(64),
    requests: 3,
    bytes: "123",
    executionState: "requires-phase-use-resource-credential-schema-images-and-linked-admission",
  };
  delete row.documentCount;
  delete row.lookupCount;
  return row;
}

it("binds context and bounded limits without copying private capture data", () => {
  expect(installPlanVerificationRequest(form, plan, charts, tree, captured)).toEqual({
    format: 1,
    artifactId: id,
    contextId: captured.id,
    contextDigest: captured.contextDigest,
    planId: plan.id,
    planDigest: plan.plan.planDigest,
    treeId: tree.id,
    treeDigest: tree.treeDigest,
    item: captured.item,
    maximumBytes: "16777216",
    maximumCaptureAgeSeconds: 3600,
    maximumCalls: 512,
    timeoutSeconds: 300,
  });
  expect(validateInstallPlanVerification(reply(), plan, charts, tree, captured, id)).toEqual(
    reply()
  );
});

it.each([plan.id, plan.operationId, charts.id, tree.id, captured.id])(
  "refuses artifact alias %s",
  (artifactId) => {
    expect(() =>
      installPlanVerificationRequest({ ...form, artifactId }, plan, charts, tree, captured)
    ).toThrow();
  }
);

it.each([
  { maximumBytes: "0" },
  { maximumBytes: 1 },
  { maximumBytes: "536870913" },
  { maximumBytes: "016777216" },
  { maximumCaptureAgeSeconds: "3601" },
  { maximumCaptureAgeSeconds: "0" },
  { maximumCaptureAgeSeconds: "3600\n" },
  { maximumCaptureAgeSeconds: true },
  { maximumCalls: "2049" },
  { maximumCalls: "1.5" },
  { timeoutSeconds: "301" },
  { credentials: "PRIVATE" },
  { continuationAuthorized: true },
])("refuses malformed verification form %j", (change) => {
  expect(() =>
    installPlanVerificationRequest({ ...form, ...change }, plan, charts, tree, captured)
  ).toThrow();
});

it.each([
  { contextId: plan.id },
  { contextVersion: captured.version + 1 },
  { contextDigest: "f".repeat(64) },
  { planVersion: plan.version + 1 },
  { chartArtifactDigest: "f".repeat(64) },
  { treeVersion: tree.version + 1 },
  { item: "controller" },
  { ordinal: 0 },
  { captureDigest: "f".repeat(64) },
  { frozenChartSha256: "f".repeat(64) },
  { artifactDigest: null },
  { verificationDigest: "PRIVATE" },
  { requests: true },
  { requests: 0 },
  { requests: 2049 },
  { bytes: "0" },
  { bytes: "536870913" },
  { bytes: 123 },
  { observedAt: "2026-11-09T00:00:00" },
  { executionState: "ready" },
  { continuationAuthorized: true },
  { privatePath: "PRIVATE" },
])("refuses unbound, private or authorizing metadata %j", (change) => {
  expect(() =>
    validateInstallPlanVerification({ ...reply(), ...change }, plan, charts, tree, captured, id)
  ).toThrow();
});

it("refuses stale parents before constructing an intent", () => {
  expect(() =>
    installPlanVerificationRequest(
      form,
      { ...plan, version: plan.version + 1 },
      charts,
      tree,
      captured
    )
  ).toThrow();
  expect(() =>
    installPlanVerificationRequest(
      form,
      plan,
      charts,
      { ...tree, version: tree.version + 1 },
      captured
    )
  ).toThrow();
});
