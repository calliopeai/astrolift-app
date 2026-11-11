import { expect, it } from "vitest";
import { INSTALL_IMAGE_BINDING_PROPS as fixture } from "@/components/screens/nvke/NvkeOperatorInstallPlanImageBindingPanel.stories";
import {
  IMMUTABLE_EXECUTION_STATE,
  IMMUTABLE_PAYLOAD_STATE,
  installPlanImmutableRequest,
  parsePayloadProofBindings,
  validateInstallPlanImmutableArtifact,
  type NvkeImmutableMode,
} from "./operator-install-plan-immutable";

const plan = fixture.plan!,
  charts = fixture.charts!,
  tree = fixture.tree!,
  workload = fixture.workload!,
  binding = fixture.artifacts!;
const id = "709813b1-a359-465f-bc15-41b648ece1b1";
const phases = Object.fromEntries(
  Array.from({ length: plan.itemCount }, (_, i) => [
    `NKTP_Phase_${i}`,
    {
      artifactId: `f0000000-0000-4000-8000-${String(i + 1).padStart(12, "0")}`,
      artifactDigest: "9".repeat(64),
    },
  ])
);
const form = (mode: NvkeImmutableMode) => ({
  artifactId: id,
  item: mode === "payload" ? "NKTP_Phase_0" : "",
  payloadBindings: mode === "execution" ? JSON.stringify(phases) : "",
  maximumBytes: mode === "payload" ? "67108864" : "9223372036854775807",
  maximumResources: mode === "payload" ? "100" : "",
  timeoutSeconds: "300",
});
const commonKeys =
  "version status planId planVersion planDigest chartArtifactId chartArtifactVersion chartArtifactDigest treeId treeVersion treeDigest operationId operationVersion siteId siteVersion authorityId authorityVersion workloadId workloadVersion workloadDigest".split(
    " "
  );
function artifact(mode: NvkeImmutableMode) {
  const common = Object.fromEntries(
    commonKeys.map((key) => [key, binding[key as keyof typeof binding]])
  );
  return {
    ...common,
    id,
    artifactDigest: "1".repeat(64),
    sourceDigest: "2".repeat(64),
    captureDigest: "3".repeat(64),
    bindingId: binding.id,
    bindingVersion: binding.version,
    bindingDigest: binding.bindingDigest,
    fileCount: 1,
    observedAt: binding.observedAt,
    sourceObservedAt: binding.observedAt,
    continuationAuthorized: false,
    ...(mode === "payload"
      ? {
          payloadDigest: "8".repeat(64),
          payloadBytes: "64",
          resourceCount: 1,
          imageUseCount: 1,
          rewriteCount: 1,
          executionState: IMMUTABLE_PAYLOAD_STATE,
        }
      : {
          executionDigest: "7".repeat(64),
          bytes: "128",
          phaseCount: plan.itemCount - plan.plan.firstRemainingOrdinal,
          skippedPhaseCount: plan.plan.firstRemainingOrdinal,
          importCount: 0,
          executionState: IMMUTABLE_EXECUTION_STATE,
        }),
  };
}
const request = (values: unknown, mode: NvkeImmutableMode) =>
  installPlanImmutableRequest(values, mode, plan, charts, tree, workload, binding);
const validate = (value: unknown, mode: NvkeImmutableMode) =>
  validateInstallPlanImmutableArtifact(value, mode, plan, charts, tree, workload, binding, id);

it.each(["payload", "execution"] as const)(
  "binds %s to the exact source and preserves decimal budgets",
  (mode) => {
    const body = request(form(mode), mode);
    expect(body.bindingId).toBe(binding.id);
    expect(body.bindingDigest).toBe(binding.bindingDigest);
    expect(body.maximumBytes).toBe(form(mode).maximumBytes);
    expect(body.timeoutSeconds).toBe(300);
    expect("payloadBindings" in body).toBe(mode === "execution");
    expect("item" in body).toBe(mode === "payload");
    expect(validate(artifact(mode), mode)).toEqual(artifact(mode));
  }
);
it.each(["payload", "execution"] as const)(
  "refuses stale %s binding before creating an intent",
  (mode) => {
    expect(() =>
      installPlanImmutableRequest(form(mode), mode, plan, charts, tree, workload, {
        ...binding,
        workloadVersion: binding.workloadVersion + 1,
      })
    ).toThrow();
    expect(() => request({ ...form(mode), artifactId: binding.id }, mode)).toThrow();
  }
);
it.each(["payload", "execution"] as const)(
  "refuses source drift, continuation and malformed %s metadata",
  (mode) => {
    for (const change of [
      { bindingVersion: binding.version + 1 },
      { workloadDigest: "f".repeat(64) },
      { continuationAuthorized: true },
      { fileCount: undefined },
      { sourceObservedAt: "2000-01-01T00:00:00Z" },
      { captureDigest: "invalid" },
      { privatePath: "PRIVATE_DO_NOT_PRINT" },
      { executionState: mode === "payload" ? IMMUTABLE_EXECUTION_STATE : IMMUTABLE_PAYLOAD_STATE },
      mode === "payload" ? { resourceCount: 0 } : { phaseCount: 0 },
      mode === "payload" ? { bytes: "128" } : { payloadBytes: "64" },
    ])
      expect(() => validate({ ...artifact(mode), ...change }, mode)).toThrow();
  }
);
it("requires every phase once and refuses parent or duplicate payload identities", () => {
  expect(() => request({ ...form("execution"), payloadBindings: "{}" }, "execution")).toThrow();
  const first = Object.keys(phases)[0];
  expect(() =>
    request(
      {
        ...form("execution"),
        payloadBindings: JSON.stringify({
          ...phases,
          [first]: { artifactId: tree.id, artifactDigest: "9".repeat(64) },
        }),
      },
      "execution"
    )
  ).toThrow();
  if (plan.itemCount > 1) {
    const names = Object.keys(phases);
    expect(() =>
      request(
        {
          ...form("execution"),
          payloadBindings: JSON.stringify({ ...phases, [names[1]]: phases[names[0]] }),
        },
        "execution"
      )
    ).toThrow();
  }
});
it.each([
  '{"phase":{"artifactId":"a","artifactId":"b","artifactDigest":"c"}}',
  '{"phase":{"artifactId":"a","\\u0061rtifactId":"b","artifactDigest":"c"}}',
  '{"phase":{"ArtifactId":"a","artifactDigest":"c"}}',
  '{"phase":{"artifactId":"a","artifactDigest":"c","privatePath":"secret"}}',
  '{"invalid/phase":{"artifactId":"a","artifactDigest":"c"}}',
  "[]",
  "null",
  "{}",
])("rejects duplicate, aliased and private phase selections", (value) => {
  expect(() => parsePayloadProofBindings(value)).toThrow();
});
it.each(["payload", "execution"] as const)(
  "enforces %s form bounds and explicit stage controls",
  (mode) => {
    for (const change of [
      { maximumBytes: "0" },
      { maximumBytes: "01" },
      { maximumBytes: "9223372036854775808" },
      { timeoutSeconds: "301" },
      { timeoutSeconds: "1.5" },
      mode === "payload" ? { maximumResources: "10001" } : { item: "unexpected" },
    ]) {
      expect(() => request({ ...form(mode), ...change }, mode)).toThrow();
    }
    expect(() =>
      validateInstallPlanImmutableArtifact(
        artifact(mode),
        mode,
        plan,
        charts,
        tree,
        workload,
        binding,
        id,
        { ...form(mode), maximumBytes: "1" }
      )
    ).toThrow();
  }
);
