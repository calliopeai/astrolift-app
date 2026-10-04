import { describe, expect, it } from "vitest";
import type {
  NativeModelSourceFieldsFragment,
  RegisterBedrockModelConnectionInput,
} from "@/graphql/__generated__/operations";
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
    expect(modelSourceMode({ ...nativeModel, nativeSource: null })).toBe("unsupported");
    expect(modelSourceMode({ sourceKind: "huggingface", nativeSource: null })).toBe("hosted");
  });
});
