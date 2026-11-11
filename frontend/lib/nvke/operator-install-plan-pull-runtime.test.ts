import { expect, it } from "vitest";
import { INSTALL_PULL_SECRET_ANALYSIS_PROPS as fixture } from "@/components/screens/nvke/NvkeOperatorInstallPlanPullSecretAnalysisPanel.stories";
import { INSTALL_NODE_PLATFORMS_PROPS as nodes } from "@/components/screens/nvke/NvkeOperatorInstallPlanNodePlatformsPanel.stories";
import {
  installPlanPullRuntimeSource,
  validateInstallPlanPullRuntime,
  PULL_RUNTIME_STATE,
} from "./operator-install-plan-pull-runtime";

const source = () =>
  installPlanPullRuntimeSource(
    fixture.plan!,
    fixture.charts!,
    fixture.tree!,
    fixture.workload!,
    fixture.binding!,
    fixture.pullAuthority!,
    fixture.artifacts!,
    nodes.artifacts!
  );
const result = () => ({
  analysis: fixture.artifacts!,
  nodePlatforms: nodes.artifacts!,
  correspondenceDigest: "a".repeat(64),
  imageUseCount: fixture.artifacts!.imageUseCount,
  nodeCount: 1,
  sourceObservedAt: fixture.artifacts!.sourceObservedAt,
  nodeObservedAt: nodes.artifacts!.observedAt,
  executionState: PULL_RUNTIME_STATE,
  continuationAuthorized: false,
});

it("binds both complete current parent captures and historical observations", () => {
  expect(validateInstallPlanPullRuntime(result(), source())).toEqual(result());
});

it.each([
  { continuationAuthorized: true },
  { nodeCount: 0 },
  { nodeCount: 3 },
  { imageUseCount: 1 },
  { imageUseCount: true },
  { correspondenceDigest: "A".repeat(64) },
  { nodeObservedAt: "1900-01-01T00:00:00Z" },
  { nodeObservedAt: "2999-01-01T00:00:00Z" },
  { sourceObservedAt: "1900-01-01T00:00:00Z" },
  { credential: "private" },
  { executionState: "ready" },
])("refuses changed counts, times, continuation or private fields: %j", (change) => {
  expect(() => validateInstallPlanPullRuntime({ ...result(), ...change }, source())).toThrow();
});

it.each(["analysis", "nodePlatforms"] as const)("refuses changed %s metadata", (key) => {
  for (const change of [
    { version: 999 },
    { artifactDigest: "0".repeat(64) },
    { captureDigest: "0".repeat(64) },
    { operationVersion: 999 },
    { bindingVersion: 999 },
    { privateExtra: "private" },
  ])
    expect(() =>
      validateInstallPlanPullRuntime(
        {
          ...result(),
          [key]: { ...result()[key], ...change },
        },
        source()
      )
    ).toThrow();
});

it("rejects a node observation from another binding before review", () => {
  expect(() =>
    installPlanPullRuntimeSource(
      fixture.plan!,
      fixture.charts!,
      fixture.tree!,
      fixture.workload!,
      fixture.binding!,
      fixture.pullAuthority!,
      fixture.artifacts!,
      { ...nodes.artifacts!, bindingDigest: "0".repeat(64) }
    )
  ).toThrow();
});

it("allows Apollo typename metadata without changing the selected capture", () => {
  expect(
    validateInstallPlanPullRuntime(
      {
        ...result(),
        __typename: "NvkeOperatorInstallPlanPullRuntimeType",
        analysis: {
          ...result().analysis,
          __typename: "NvkeOperatorInstallPlanPullSecretAnalysisType",
        },
        nodePlatforms: {
          ...result().nodePlatforms,
          __typename: "NvkeOperatorInstallPlanNodePlatformsType",
        },
      },
      source()
    ).nodeCount
  ).toBe(1);
});
