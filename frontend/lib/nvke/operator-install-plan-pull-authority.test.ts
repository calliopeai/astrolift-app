import { expect, it } from "vitest";
import { INSTALL_PULL_AUTHORITY_PROPS as fixture } from "@/components/screens/nvke/NvkeOperatorInstallPlanPullAuthorityPanel.stories";
import {
  installPlanPullAuthorityRequest,
  parsePullAuthorityScope,
  validateInstallPlanPullAuthority,
  type NvkeInstallPlanPullAuthorityForm,
} from "./operator-install-plan-pull-authority";

const plan = fixture.plan!,
  charts = fixture.charts!,
  tree = fixture.tree!,
  workload = fixture.workload!,
  binding = fixture.binding!,
  id = fixture.artifacts!.id;
const form = (): NvkeInstallPlanPullAuthorityForm => ({
  artifactId: id,
  selectedPods: JSON.stringify({ "namespace/pod-b": "uid-b", "namespace/pod-a": "uid-a" }),
  maximumBytes: "67108864",
  maximumCalls: "512",
  timeoutSeconds: "300",
});
const request = (value: unknown) =>
  installPlanPullAuthorityRequest(value, plan, charts, tree, workload, binding);
const validate = (value: unknown, limits?: NvkeInstallPlanPullAuthorityForm) =>
  validateInstallPlanPullAuthority(value, plan, charts, tree, workload, binding, id, limits);

it("binds the exact source and selected live Pod UIDs without defaulting credentials", () => {
  const body = request(form());
  expect(Object.keys(body)).toHaveLength(12);
  expect(body.maximumBytes).toBe("67108864");
  expect(body.maximumCalls).toBe(512);
  expect(body.bindingId).toBe(binding.id);
  expect(Object.keys(body.selectedPods)).toEqual(["namespace/pod-a", "namespace/pod-b"]);
  expect(validate(fixture.artifacts, form())).toEqual(fixture.artifacts);
});
it.each([
  { artifactId: binding.id },
  { maximumBytes: 67108864 },
  { maximumBytes: "01" },
  { maximumBytes: "67108865" },
  { maximumBytes: "12\n" },
  { maximumCalls: "5" },
  { maximumCalls: "32769" },
  { timeoutSeconds: "301" },
  { timeoutSeconds: "0" },
  { kubeconfig: "/PRIVATE" },
  { selectedPods: "{}" },
  { selectedPods: "[]" },
])("refuses invalid, aliased, or insufficient bounded requests: %j", (change) =>
  expect(() => request({ ...form(), ...change })).toThrow()
);
it.each([
  '{"namespace/pod":"one","namespace/pod":"two"}',
  '{"namespace/pod":"one","namespace/p\\u006fd":"two"}',
  '{"namespace/pod":{"uid":"one","uid":"two"}}',
])("refuses duplicate and escaped duplicate selection keys", (text) =>
  expect(() => parsePullAuthorityScope(text)).toThrow()
);
it.each([
  { "namespace/pod": "same", "namespace/other": "same" },
  { "namespace/pod": "uid\n" },
  { "namespace/pod": null },
  { "namespace/pod": { uid: "x" } },
  { "name.space/pod": "uid" },
  { "namespace/Pod": "uid" },
  { "namespace/pod/extra": "uid" },
  { "namespace/pod\n": "uid" },
  { namespace: "uid" },
  { "/pod": "uid" },
  { ["a".repeat(64) + "/pod"]: "uid" },
  { ["namespace/" + "a".repeat(254)]: "uid" },
  { "namespace/pod": "a".repeat(129) },
])("refuses malformed selected Pods and private control aliases: %j", (scope) =>
  expect(() => parsePullAuthorityScope(JSON.stringify(scope))).toThrow()
);
it("accepts the exact name bounds and rejects oversized input", () => {
  const key = "a".repeat(63) + "/" + "b".repeat(253);
  expect(parsePullAuthorityScope(JSON.stringify({ [key]: "uid" }))).toEqual({ [key]: "uid" });
  expect(() => parsePullAuthorityScope(" ".repeat(1048577))).toThrow();
});
it.each([
  { bindingVersion: binding.version + 1 },
  { workloadDigest: "f".repeat(64) },
  { continuationAuthorized: true },
  { podCount: 0 },
  { podCount: 3 },
  { serviceAccountCount: 3 },
  { secretCount: 32768 },
  { secretCount: 9007199254740992 },
  { missingReferenceCount: 4 },
  { observedAt: "2000-01-01T00:00:00Z" },
  { authorityDigest: "1".repeat(64) + "\n" },
  { selectedPods: { "PRIVATE/pod": "uid" } },
  { credentials: "PRIVATE" },
  { secretCount: null },
  { executionState: "ready" },
  { sourceObservedAt: binding.observedAt },
])("refuses stale, contradictory, or private response metadata: %j", (change) =>
  expect(() => validate({ ...fixture.artifacts, ...change }, form())).toThrow()
);
it("refuses changed ancestry and evidence beyond the original request scope or budget", () => {
  expect(() =>
    installPlanPullAuthorityRequest(form(), plan, charts, tree, workload, {
      ...binding,
      workloadVersion: binding.workloadVersion + 1,
    })
  ).toThrow();
  expect(() => validate(fixture.artifacts, { ...form(), maximumCalls: "6" })).toThrow();
  expect(() =>
    validate(fixture.artifacts, { ...form(), selectedPods: '{"namespace/pod":"uid"}' })
  ).toThrow();
});
