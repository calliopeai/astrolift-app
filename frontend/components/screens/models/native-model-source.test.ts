import { describe, expect, it } from "vitest";
import type {
  NativeModelSourceFieldsFragment,
  RegisterBedrockModelConnectionInput,
} from "@/graphql/__generated__/operations";
import projections from "./native-model-projection.fixture.json";
import { nativeModel, nativeSource, projectedNativeModel } from "./native-model.fixtures";
import {
  modelSourceMode,
  nativeRegistrationResult,
  sameNativeSource,
  sameNativeConnection,
  nativeModelFamily,
  validNativeSource,
} from "./native-model-source";

const input: RegisterBedrockModelConnectionInput = {
  organizationId: nativeModel.organizationId,
  clusterId: nativeModel.clusterId,
  expectedProviderId: nativeModel.providerId,
  expectedClusterVersion: 2,
  expectedProviderVersion: 3,
  sourceKind: "FOUNDATION_MODEL",
  sourceIdentifier: nativeSource.identity.sourceId,
  sourceFingerprint: nativeSource.identity.sourceFingerprint,
  name: nativeModel.name,
  allowSubscriptions: true,
  sharingMode: "SHARED",
  dedicatedAppId: null,
  ifMatchDedicatedAppVersion: null,
};
describe("native source boundaries", () => {
  it("accepts configured metadata without manufacturing runtime readiness", () => {
    expect(validNativeSource(nativeSource.identity)).toBe(true);
    expect(modelSourceMode(nativeModel)).toBe("native");
    expect(
      nativeRegistrationResult(
        { ok: true, errors: [], data: nativeModel },
        input,
        nativeSource.identity,
        "refused"
      )
    ).toEqual({ accepted: true, id: nativeModel.id });
  });
  it.each([
    { protocol: "OTHER" },
    { sourceArn: "arn:aws:bedrock:us-west-2::foundation-model/amazon.titan-text-express-v1" },
    { accountId: "other" },
    { partition: "foreign" },
    { sourceFingerprint: "invalid" },
    { invokeAccess: "authorized" },
    { configurationState: "ready" },
    { sourceKind: "OTHER" },
    { sourceArn: 123 },
    { destinationModelArns: [123] },
  ])("refuses malformed or unknown native projection %j", (change) => {
    const identity = { ...nativeSource.identity, ...change } as NativeModelSourceFieldsFragment;
    expect(validNativeSource(identity)).toBe(false);
    expect(modelSourceMode({ ...nativeModel, nativeSource: identity })).toBe("unsupported");
  });
  it.each([
    { organizationId: "other" },
    { clusterId: "other" },
    { providerId: "other" },
    { ready: true },
    { runtimeSupported: true },
    { operationId: "fake-runtime" },
    { status: "updating" },
    { name: "other" },
    { version: 0 },
    { dedicatedAppId: "other" },
    { sourceKind: "huggingface" },
  ])("refuses unrelated or fabricated registration outcome %j", (change) => {
    expect(
      nativeRegistrationResult(
        { ok: true, errors: [], data: { ...nativeModel, ...change } },
        input,
        nativeSource.identity,
        "refused"
      )
    ).toEqual({ accepted: false, message: "refused" });
  });
  it("preserves immutable identity across metadata observation refresh", () => {
    expect(
      sameNativeSource(nativeSource.identity, {
        ...nativeSource.identity,
        metadataObservedAt: null,
        configurationState: "unavailable",
      })
    ).toBe(true);
    expect(
      sameNativeSource(nativeSource.identity, {
        ...nativeSource.identity,
        sourceFingerprint: "b".repeat(64),
      })
    ).toBe(false);
  });
  it("never treats missing native identity as a hosted model", () => {
    expect(modelSourceMode(projectedNativeModel("withdrawn"))).toBe("native_unavailable");
    expect(modelSourceMode({ ...nativeModel, nativeSource: null })).toBe("unsupported");
    expect(modelSourceMode({ sourceKind: "huggingface", nativeSource: null })).toBe("hosted");
    expect(
      modelSourceMode({ sourceKind: "huggingface", nativeSource: nativeSource.identity })
    ).toBe("unsupported");
  });
});

it("accepts the exact inference-profile discriminator and refuses cross-kind metadata", () => {
  const model = projectedNativeModel("profile");
  const identity = model.nativeSource!;
  expect(modelSourceMode(model)).toBe("native");
  expect(
    nativeRegistrationResult(
      { ok: true, errors: [], data: model },
      {
        ...input,
        sourceKind: "INFERENCE_PROFILE",
        sourceIdentifier: identity.sourceId,
        sourceFingerprint: identity.sourceFingerprint,
      },
      identity,
      "refused"
    )
  ).toEqual({ accepted: true, id: model.id });
  expect(modelSourceMode({ ...model, nativeSource: null })).toBe("unsupported");
  expect(modelSourceMode({ ...model, sourceKind: "bedrock_foundation_model" })).toBe("unsupported");
  expect(modelSourceMode({ ...model, sourceKind: "bedrock_unknown", nativeSource: null })).toBe(
    "unsupported"
  );
  expect(modelSourceMode({ ...model, sourceKind: "bedrock" })).toBe("unsupported");
});

it.each(projections.rows)(
  "consumes actual serialized producer $case with legacy equivalence",
  ({ case: variant, serializedQueryData: wire, ...row }) => {
    const projected = projectedNativeModel(variant);
    expect(modelSourceMode(projected)).toBe(
      wire.nativeConnection.configurationState === "CONFIGURED" ? "native" : "native_unavailable"
    );
    expect(nativeModelFamily(projected)).toBe(wire.nativeConnection.family);
    if ("legacyProjection" in row)
      expect(row.legacyProjection).toEqual({
        sourceKind: wire.sourceKind,
        nativeSource: wire.nativeSource,
      });
  }
);

it.each([
  { nativeConnection: null },
  { nativeConnection: undefined },
  { nativeConnection: { ...nativeModel.nativeConnection!, family: "VERTEX" } },
  {
    nativeConnection: { ...nativeModel.nativeConnection!, sourceKind: "BEDROCK_INFERENCE_PROFILE" },
  },
  { nativeConnection: { ...nativeModel.nativeConnection!, configurationState: "READY" } },
  { nativeConnection: { ...nativeModel.nativeConnection!, invokeAccess: "AUTHORIZED" } },
  {
    nativeConnection: {
      ...nativeModel.nativeConnection!,
      resourceIdentityFingerprint: "private-url-marker",
    },
  },
  {
    nativeConnection: {
      ...nativeModel.nativeConnection!,
      reviewedSourceFingerprint: "b".repeat(64),
    },
  },
  { nativeConnection: { ...nativeModel.nativeConnection!, metadataObservedAt: "invalid" } },
  {
    nativeConnection: {
      ...nativeModel.nativeConnection!,
      source: {
        ...nativeModel.nativeConnection!.source!,
        __typename: "FoundryDeploymentConnectionSource",
      },
    },
  },
])("refuses malformed or inconsistent advertised common contract %j", (change) => {
  const model = { ...nativeModel, ...change } as typeof nativeModel;
  expect(modelSourceMode(model)).toBe("unsupported");
  expect(
    nativeRegistrationResult(
      { ok: true, errors: [], data: model },
      input,
      nativeSource.identity,
      "refused"
    ).accepted
  ).toBe(false);
});

it("requires explicit absent capability for legacy compatibility and binds exact common identity", () => {
  const old = { sourceKind: nativeModel.sourceKind, nativeSource: nativeModel.nativeSource };
  expect(modelSourceMode(old)).toBe("unsupported");
  expect(modelSourceMode(old, false)).toBe("native");
  expect(modelSourceMode({ ...old, nativeConnection: null }, false)).toBe("unsupported");
  expect(sameNativeConnection(nativeModel, projectedNativeModel("foundation"))).toBe(true);
  expect(
    sameNativeConnection(nativeModel, {
      ...nativeModel,
      nativeConnection: {
        ...nativeModel.nativeConnection!,
        resourceIdentityFingerprint: "b".repeat(64),
      },
    })
  ).toBe(false);
});

it.each(["vertex_unadopted", "foundry_unadopted", "unknown_family"])(
  "refuses invented configured adoption for %s",
  (variant) => {
    const model = projectedNativeModel(variant);
    expect(
      modelSourceMode({
        ...model,
        nativeConnection: { ...model.nativeConnection!, configurationState: "CONFIGURED" },
      })
    ).toBe("unsupported");
    expect(modelSourceMode({ ...model, sourceKind: "huggingface" })).toBe("unsupported");
  }
);
