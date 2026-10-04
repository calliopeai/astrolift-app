import { describe, expect, it } from "vitest";
import type {
  NativeModelSourceFieldsFragment,
  RegisterBedrockModelConnectionInput,
} from "@/graphql/__generated__/operations";
import projections from "./native-model-projection.fixture.json";
import { nativeModel, nativeSource } from "./native-model.fixtures";
import {
  modelSourceMode,
  nativeRegistrationResult,
  sameNativeSource,
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
    expect(modelSourceMode({ ...nativeModel, nativeSource: null })).toBe("native_unavailable");
    expect(modelSourceMode({ sourceKind: "huggingface", nativeSource: null })).toBe("hosted");
    expect(
      modelSourceMode({ sourceKind: "huggingface", nativeSource: nativeSource.identity })
    ).toBe("unsupported");
  });
});

it("accepts the exact inference-profile discriminator and refuses cross-kind metadata", () => {
  const identity: NativeModelSourceFieldsFragment = {
    ...nativeSource.identity,
    sourceKind: "INFERENCE_PROFILE",
    sourceId: "reviewed-profile",
    sourceArn: `arn:aws:bedrock:us-east-1:${nativeSource.identity.accountId}:inference-profile/reviewed-profile`,
    destinationModelArns: [nativeSource.identity.sourceArn],
  };
  const model = { ...nativeModel, sourceKind: "bedrock_inference_profile", nativeSource: identity };
  expect(modelSourceMode(model)).toBe("native");
  expect(
    nativeRegistrationResult(
      { ok: true, errors: [], data: model },
      { ...input, sourceKind: "INFERENCE_PROFILE", sourceIdentifier: identity.sourceId },
      identity,
      "refused"
    )
  ).toEqual({ accepted: true, id: model.id });
  expect(modelSourceMode({ ...model, nativeSource: null })).toBe("native_unavailable");
  expect(modelSourceMode({ ...model, sourceKind: "bedrock_foundation_model" })).toBe("unsupported");
  expect(modelSourceMode({ ...model, sourceKind: "bedrock_unknown", nativeSource: null })).toBe(
    "unsupported"
  );
  expect(modelSourceMode({ ...model, sourceKind: "bedrock" })).toBe("unsupported");
});

it.each(projections.rows)(
  "consumes actual serialized backend projection $case",
  ({ case: variant, serializedQueryData: wire }) => {
    const base =
      wire.nativeSource?.sourceKind === "INFERENCE_PROFILE"
        ? {
            ...nativeSource.identity,
            sourceKind: "INFERENCE_PROFILE" as const,
            sourceId: "reviewed-profile",
            sourceArn: `arn:aws:bedrock:us-east-1:${nativeSource.identity.accountId}:inference-profile/reviewed-profile`,
            destinationModelArns: [nativeSource.identity.sourceArn],
          }
        : nativeSource.identity;
    const projected = {
      sourceKind: wire.sourceKind,
      nativeSource: wire.nativeSource
        ? ({ ...base, ...wire.nativeSource } as NativeModelSourceFieldsFragment)
        : null,
    };
    expect(modelSourceMode(projected)).toBe(
      variant === "withdrawn"
        ? "native_unavailable"
        : variant === "unknown"
          ? "unsupported"
          : "native"
    );
    if (variant === "foundation") {
      expect(nativeModel.sourceKind).toBe(wire.sourceKind);
      expect(nativeSource.identity.protocol).toBe(wire.nativeSource!.protocol);
      expect(nativeSource.identity.sourceKind).toBe(wire.nativeSource!.sourceKind);
    }
  }
);
