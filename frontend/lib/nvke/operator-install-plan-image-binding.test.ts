import { expect, it } from "vitest";
import { INSTALL_IMAGE_BINDING_PROPS as fixture } from "@/components/screens/nvke/NvkeOperatorInstallPlanImageBindingPanel.stories";
import {
  installPlanImageBindingRequest,
  parseImageProofBindings,
  validateInstallPlanImageBinding,
} from "./operator-install-plan-image-binding";
const plan = fixture.plan!,
  charts = fixture.charts!,
  tree = fixture.tree!,
  workload = fixture.workload!,
  row = fixture.artifacts!;
const image = "45e44f42-edf6-4731-bad4-245974936df2";
const selection = { sourceKind: "registry", artifactId: image, artifactDigest: "1".repeat(64) };
const form = {
  artifactId: row.id,
  imageBindings: JSON.stringify({ "private.example.test/owned:v1": selection }),
  maximumImageBytes: "268435456",
  maximumBytes: "16777216",
  timeoutSeconds: "300",
};
it("binds private image selections to the exact workload and validates bounded public metadata", () => {
  const body = installPlanImageBindingRequest(form, plan, charts, tree, workload);
  expect(body.workloadId).toBe(workload.id);
  expect(body.workloadDigest).toBe(workload.workloadDigest);
  expect(body.imageBindings).toEqual(JSON.parse(form.imageBindings));
  expect(validateInstallPlanImageBinding(row, plan, charts, tree, workload, row.id, form)).toEqual(
    row
  );
});
it.each([
  '{"a":{"sourceKind":"registry","sourceKind":"local","artifactId":"' +
    image +
    '","artifactDigest":"' +
    "1".repeat(64) +
    '"}}',
  JSON.stringify({ a: { ...selection, SourceKind: "registry" } }),
  JSON.stringify({ a: { ...selection, path: "PRIVATE" } }),
  JSON.stringify({ "https://private.test/image": selection }),
  "null",
  "[]",
  "{}{}",
])("refuses ambiguous or private selections: %s", (imageBindings) => {
  expect(() =>
    installPlanImageBindingRequest({ ...form, imageBindings }, plan, charts, tree, workload)
  ).toThrow();
});
it.each([
  { artifactId: workload.id },
  { maximumImageBytes: "0" },
  { maximumImageBytes: "9223372036854775808" },
  { maximumBytes: "67108865" },
  { timeoutSeconds: "301" },
  { credentials: "PRIVATE" },
])("refuses an invalid bound request: %j", (change) => {
  expect(() =>
    installPlanImageBindingRequest({ ...form, ...change }, plan, charts, tree, workload)
  ).toThrow();
});
it("sorts reference keys for stable exact-intent recovery and forbids selected parent identities", () => {
  expect(
    Object.keys(
      parseImageProofBindings(
        JSON.stringify({ "z.test/a:v1": selection, "a.test/b:v1": selection })
      )
    )
  ).toEqual(["a.test/b:v1", "z.test/a:v1"]);
  expect(() =>
    installPlanImageBindingRequest(
      {
        ...form,
        imageBindings: JSON.stringify({
          "private.test/a:v1": { ...selection, artifactId: row.id },
        }),
      },
      plan,
      charts,
      tree,
      workload
    )
  ).toThrow();
});
it.each([
  { workloadVersion: workload.version + 1 },
  { workloadDigest: "9".repeat(64) },
  { imageUseCount: 4 },
  { workloadIssueCount: 1 },
  { rewriteCount: 2 },
  { verifiedImageBytes: "0" },
  { continuationAuthorized: true },
  { imageBindings: {} },
  { bindingDigest: "invalid" },
])("refuses changed, private or authorizing metadata: %j", (change) => {
  expect(() =>
    validateInstallPlanImageBinding({ ...row, ...change }, plan, charts, tree, workload, row.id)
  ).toThrow();
});
it("refuses output beyond the exact reviewed byte and selection budgets", () => {
  expect(() =>
    validateInstallPlanImageBinding(row, plan, charts, tree, workload, row.id, {
      ...form,
      maximumImageBytes: "1",
    })
  ).toThrow();
  expect(() =>
    validateInstallPlanImageBinding(row, plan, charts, tree, workload, row.id, {
      ...form,
      imageBindings: "{}",
    })
  ).toThrow();
});
