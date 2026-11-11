import { expect, it } from "vitest";
import { INSTALL_PULL_AUTHORITY_PROPS as fixture } from "@/components/screens/nvke/NvkeOperatorInstallPlanPullAuthorityPanel.stories";
import {
  installPlanPullSecretAnalysisRequest,
  installPlanPullSecretAnalysisSchema,
  parsePullSecretAnalysisSelections,
  validateInstallPlanPullSecretAnalysis,
  PULL_SECRET_ANALYSIS_STATE,
  type NvkeInstallPlanPullSecretAnalysisForm,
} from "./operator-install-plan-pull-secret-analysis";

const plan = fixture.plan!,
  charts = fixture.charts!,
  tree = fixture.tree!,
  workload = fixture.workload!,
  binding = fixture.binding!,
  parent = fixture.artifacts!;
const id = "31d86755-ef24-450e-a1e6-c6a06906cc95";
const selected = [
  {
    podKey: "namespace/pod-a",
    imagePointer: "/spec/containers/0/image",
    ensureSecretPulledImages: true,
  },
  {
    podKey: "namespace/pod-a",
    imagePointer: "/spec/initContainers/0/image",
    ensureSecretPulledImages: false,
  },
];
const form = (): NvkeInstallPlanPullSecretAnalysisForm => ({
  artifactId: id,
  imageSelections: JSON.stringify(selected),
  maximumBytes: "16777216",
  maximumHelperBytes: "1048576",
  timeoutSeconds: "300",
});
const metadata = () =>
  installPlanPullSecretAnalysisSchema.parse({
    ...Object.fromEntries(
      Object.entries(parent).filter(
        ([key]) =>
          ![
            "observedAt",
            "podCount",
            "serviceAccountCount",
            "secretCount",
            "missingReferenceCount",
            "executionState",
            "__typename",
          ].includes(key)
      )
    ),
    id,
    pullAuthorityId: parent.id,
    pullAuthorityVersion: parent.version,
    pullAuthorityDigest: parent.authorityDigest,
    analyzedAt: parent.observedAt,
    sourceObservedAt: parent.observedAt,
    imageUseCount: 2,
    candidateCount: 2,
    missingReferenceCount: 1,
    executionState: PULL_SECRET_ANALYSIS_STATE,
  });
const request = (value: unknown) =>
  installPlanPullSecretAnalysisRequest(value, plan, charts, tree, workload, binding, parent);
const validate = (value: unknown, limits?: NvkeInstallPlanPullSecretAnalysisForm) =>
  validateInstallPlanPullSecretAnalysis(
    value,
    plan,
    charts,
    tree,
    workload,
    binding,
    parent,
    id,
    limits
  );

it("binds the exact admitted observation and explicit feature choices without credential inputs", () => {
  const body = request(form());
  expect(Object.keys(body)).toHaveLength(14);
  expect(body.pullAuthorityId).toBe(parent.id);
  expect(body.pullAuthorityDigest).toBe(parent.authorityDigest);
  expect(body.maximumHelperBytes).toBe("1048576");
  expect(body.imageSelections).toEqual(selected);
  expect(validate(metadata(), form())).toEqual(metadata());
});
it.each([
  { artifactId: parent.id },
  { artifactId: binding.id },
  { maximumBytes: 1 },
  { maximumBytes: "0" },
  { maximumBytes: "01" },
  { maximumBytes: "1\n" },
  { maximumBytes: "67108865" },
  { maximumBytes: "1e3" },
  { maximumHelperBytes: "16777217" },
  { maximumHelperBytes: "0" },
  { maximumHelperBytes: "1\n" },
  { timeoutSeconds: "301" },
  { helperProgram: "/PRIVATE" },
  { credential: "PRIVATE" },
  { imageSelections: "[]" },
  { imageSelections: "{}" },
])("refuses invalid identity, private paths and unbounded or coerced values: %j", (change) => {
  expect(() => request({ ...form(), ...change })).toThrow();
});
it.each([
  { imagePointer: "/status/image" },
  { imagePointer: "/spec/containers/00/image" },
  { imagePointer: "/spec/containers/-1/image" },
  { imagePointer: "/spec/containers/0/image\n" },
  { podKey: "namespace/pod\n" },
  { podKey: "name.space/pod" },
  { podKey: "../pod" },
  { ensureSecretPulledImages: 1 },
  { ensureSecretPulledImages: "true" },
  { credentials: { password: "PRIVATE" } },
])(
  "refuses non-admitted image pointers, malformed names and implicit/coerced feature settings: %j",
  (change) => {
    expect(() =>
      parsePullSecretAnalysisSelections(JSON.stringify([{ ...selected[0], ...change }]))
    ).toThrow();
  }
);
it("requires an explicit feature setting and unique admitted image uses", () => {
  expect(() =>
    parsePullSecretAnalysisSelections(
      '[{"podKey":"namespace/pod","imagePointer":"/spec/containers/0/image"}]'
    )
  ).toThrow();
  expect(() =>
    parsePullSecretAnalysisSelections(
      JSON.stringify([selected[0], { ...selected[0], ensureSecretPulledImages: false }])
    )
  ).toThrow();
  expect(() =>
    parsePullSecretAnalysisSelections(
      JSON.stringify(
        Array.from({ length: 1025 }, (_, i) => ({
          ...selected[0],
          imagePointer: `/spec/containers/${i}/image`,
        }))
      )
    )
  ).toThrow();
});
it.each([
  '[{"podKey":"namespace/pod","podKey":"namespace/other","imagePointer":"/spec/containers/0/image","ensureSecretPulledImages":true}]',
  '[{"podKey":"namespace/pod","pod\\u004bey":"namespace/other","imagePointer":"/spec/containers/0/image","ensureSecretPulledImages":true}]',
  '[{"podKey":"namespace/pod","imagePointer":"/spec/containers/0/image","ensureSecretPulledImages":true,"ensureSecretPulledImages":false}]',
])("refuses duplicate and escaped duplicate selection keys", (text) =>
  expect(() => parsePullSecretAnalysisSelections(text)).toThrow()
);
it.each([
  { pullAuthorityId: binding.id },
  { pullAuthorityVersion: parent.version + 1 },
  { pullAuthorityDigest: "8".repeat(64) },
  { imageUseCount: 3 },
  { imageUseCount: 0 },
  { candidateCount: 200001 },
  { missingReferenceCount: 20001 },
  { analyzedAt: "2000-01-01T00:00:00Z" },
  { sourceObservedAt: "2000-01-01T00:00:00Z" },
  { sourceObservedAt: "2099-01-01T00:00:00Z" },
  { continuationAuthorized: true },
  { observedAt: parent.observedAt },
  { credentials: "PRIVATE" },
  { executionState: "ready" },
])("refuses stale, contradictory or private analysis metadata: %j", (change) =>
  expect(() => validate({ ...metadata(), ...change }, form())).toThrow()
);
it("preserves a zero-candidate private interpretation without authorizing continuation", () => {
  expect(validate({ ...metadata(), candidateCount: 0 }, form()).continuationAuthorized).toBe(false);
});
