import { expect, it } from "vitest";
import { INSTALL_NODE_PLATFORMS_PROPS as fixture } from "@/components/screens/nvke/NvkeOperatorInstallPlanNodePlatformsPanel.stories";
import {
  installPlanNodePlatformsRequest,
  parseNodePlatformScope,
  validateInstallPlanNodePlatforms,
  NODE_RUNTIME_PROFILES,
  type NvkeInstallPlanNodePlatformsForm,
} from "./operator-install-plan-node-platforms";

const plan = fixture.plan!,
  charts = fixture.charts!,
  tree = fixture.tree!,
  workload = fixture.workload!,
  binding = fixture.binding!,
  id = fixture.artifacts!.id;
export const NODE_PLATFORM_TEST_SCOPE = JSON.stringify({
  "worker-a.example": {
    uid: "owned-node-a",
    runtimeProfile: "containerd/v2.3.0",
    platform: { os: "linux", architecture: "arm64", variant: "v8", "os.features": ["高速"] },
  },
  "worker-b": {
    uid: "owned-node-b",
    runtimeProfile: "containerd/v1.7.31",
    platform: { os: "linux", architecture: "arm64" },
  },
});
const form = (): NvkeInstallPlanNodePlatformsForm => ({
  artifactId: id,
  nodePlatforms: NODE_PLATFORM_TEST_SCOPE,
  maximumImageBytes: "9007199254740993",
  maximumMetadataBytes: "67108864",
  maximumBytes: "16777216",
  maximumObservationBytes: "67108864",
  maximumHelperBytes: "9223372036854775807",
  maximumCalls: "512",
  maximumPairs: "100000",
  timeoutSeconds: "300",
});
const request = (value: unknown) =>
  installPlanNodePlatformsRequest(value, plan, charts, tree, workload, binding);
const validate = (value: unknown, limits?: NvkeInstallPlanNodePlatformsForm) =>
  validateInstallPlanNodePlatforms(value, plan, charts, tree, workload, binding, id, limits);

it("preserves exact source, all node UIDs and decimal int64 byte limits", () => {
  const body = request(form());
  expect(body.maximumImageBytes).toBe("9007199254740993");
  expect(body.maximumHelperBytes).toBe("9223372036854775807");
  expect(body.maximumCalls).toBe(512);
  expect(body.bindingId).toBe(binding.id);
  expect(Object.keys(body.nodePlatforms)).toEqual(["worker-a.example", "worker-b"]);
  expect(validate(fixture.artifacts, form())).toEqual(fixture.artifacts);
});
it.each(NODE_RUNTIME_PROFILES)("accepts explicit supported runtime %s", (profile) => {
  const scope = JSON.parse(NODE_PLATFORM_TEST_SCOPE);
  scope["worker-a.example"].runtimeProfile = profile;
  expect(
    request({ ...form(), nodePlatforms: JSON.stringify(scope) }).nodePlatforms["worker-a.example"]
      .runtimeProfile
  ).toBe(profile);
});
it.each([
  { artifactId: binding.id },
  { maximumImageBytes: 9007199254740993 },
  { maximumImageBytes: "01" },
  { maximumImageBytes: "9223372036854775808" },
  { maximumImageBytes: "12\n" },
  { maximumMetadataBytes: "67108865" },
  { maximumBytes: "16777217" },
  { maximumObservationBytes: "67108865" },
  { maximumCalls: "11" },
  { maximumCalls: "65537" },
  { maximumPairs: "0" },
  { timeoutSeconds: "301" },
  { helperProgram: "/PRIVATE" },
  { nodePlatforms: "{}" },
])("refuses invalid or aliased bounded requests: %j", (change) =>
  expect(() => request({ ...form(), ...change })).toThrow()
);
it.each([
  '{"worker-a":{"uid":"x","uid":"y","runtimeProfile":"containerd/v2.3.0","platform":{"os":"linux","architecture":"arm64"}}}',
  '{"worker-a":{"uid":"x","runtimeProfile":"containerd/v2.3.0","platform":{"os":"linux","\\u006fs":"linux","architecture":"arm64"}}}',
  '{"worker-a":{"uid":"x","runtimeProfile":"containerd/v2.3.0","platform":{"os":"linux","architecture":"arm64"}},"worker-\\u0061":{"uid":"y","runtimeProfile":"containerd/v2.3.0","platform":{"os":"linux","architecture":"arm64"}}}',
])("rejects escaped or nested duplicate scope keys", (text) =>
  expect(() => parseNodePlatformScope(text)).toThrow()
);
it("refuses duplicate UIDs, private controls and node/platform aliases", () => {
  const node = JSON.parse(NODE_PLATFORM_TEST_SCOPE)["worker-a.example"];
  for (const scope of [
    { one: node, two: node },
    { "worker-a\n": node },
    { node: { ...node, UID: "alias" } },
    { node: { ...node, runtimeProfile: "containerd/v99" } },
    { node: { ...node, platform: { ...node.platform, helperProgram: "/PRIVATE" } } },
    { node: { ...node, platform: { ...node.platform, "os.features": [null] } } },
  ])
    expect(() => parseNodePlatformScope(JSON.stringify(scope))).toThrow();
});
it.each([
  { bindingVersion: binding.version + 1 },
  { workloadDigest: "f".repeat(64) },
  { continuationAuthorized: true },
  { nodeCount: 0 },
  { nodeCount: 3 },
  { pairCount: 3 },
  { imageReferenceCount: binding.imageCount + 1 },
  { observedAt: "2000-01-01T00:00:00Z" },
  { platformDigest: "1".repeat(64) + "\n" },
  { sourceObservedAt: binding.observedAt },
  { nodePlatforms: "PRIVATE" },
  { credentials: "PRIVATE" },
])("refuses stale, contradictory or private response metadata: %j", (change) =>
  expect(() => validate({ ...fixture.artifacts, ...change })).toThrow()
);
it("refuses changed ancestry and prepare-result scope beyond the reviewed request", () => {
  expect(() =>
    installPlanNodePlatformsRequest(form(), plan, charts, tree, workload, {
      ...binding,
      workloadVersion: binding.workloadVersion + 1,
    })
  ).toThrow();
  expect(() => validate(fixture.artifacts, { ...form(), maximumPairs: "1" })).toThrow();
  const one = JSON.parse(NODE_PLATFORM_TEST_SCOPE);
  delete one["worker-b"];
  expect(() =>
    validate(fixture.artifacts, { ...form(), nodePlatforms: JSON.stringify(one) })
  ).toThrow();
});
