import { buildSchema, validate, getOperationAST, getVariableValues } from "graphql";
import { describe, expect, it } from "vitest";
import schemaSDL from "@/schema.graphql?raw";
import {
  PROVISION_CLUSTER_MODEL,
  SUBSCRIBE_CLUSTER_MODEL,
  REVOKE_MODEL_SUBSCRIPTION,
  UPDATE_CLUSTER_MODEL,
  DEPROVISION_CLUSTER_MODEL,
} from "@/graphql/models/shared-models.mutations";
const schema = buildSchema(schemaSDL);
const identities = {
  organizationId: "019c69ed-1e9c-7000-8000-000000000001",
  modelDeploymentId: "019c69ed-1e9c-7000-8000-000000000002",
  clusterId: "019c69ed-1e9c-7000-8000-000000000003",
  providerId: "019c69ed-1e9c-7000-8000-000000000004",
  environmentId: "019c69ed-1e9c-7000-8000-000000000005",
  subscriptionId: "019c69ed-1e9c-7000-8000-000000000006",
};
const subscription = {
  organizationId: identities.organizationId,
  modelDeploymentId: identities.modelDeploymentId,
  expectedClusterId: identities.clusterId,
  expectedProviderId: identities.providerId,
  appEnvironmentId: identities.environmentId,
  alias: "chat",
  ifMatchVersion: 7,
  ifMatchEnvironmentVersion: 3,
};
describe("frozen shared model write documents", () => {
  it.each([
    ["provision", PROVISION_CLUSTER_MODEL],
    ["subscribe", SUBSCRIBE_CLUSTER_MODEL],
    ["revoke", REVOKE_MODEL_SUBSCRIPTION],
    ["update", UPDATE_CLUSTER_MODEL],
    ["deprovision", DEPROVISION_CLUSTER_MODEL],
  ])("validates %s against the complete composed SDL", (_name, document) =>
    expect(validate(schema, document)).toEqual([])
  );
  it.each([
    "expectedClusterId",
    "expectedProviderId",
    "ifMatchVersion",
    "ifMatchEnvironmentVersion",
  ])("refuses subscription variables missing actual required %s", (field) => {
    const input: { [key: string]: unknown } = { ...subscription };
    delete input[field];
    const operation = getOperationAST(SUBSCRIBE_CLUSTER_MODEL)!;
    const result = getVariableValues(schema, operation.variableDefinitions ?? [], { input });
    expect(result.errors?.map((error) => error.message).join("\n")).toContain(
      `Field "${field}" of required type`
    );
  });
  it("admits actual named subscription variables with both version identities", () => {
    const operation = getOperationAST(SUBSCRIBE_CLUSTER_MODEL)!;
    const result = getVariableValues(schema, operation.variableDefinitions ?? [], {
      input: subscription,
    });
    expect(result.errors).toBeUndefined();
    expect(result.coerced?.input).toEqual(subscription);
  });
  it("keeps deletion data retention at the actual safe server default", () => {
    const operation = getOperationAST(DEPROVISION_CLUSTER_MODEL)!;
    const input = {
      organizationId: identities.organizationId,
      id: identities.modelDeploymentId,
      expectedClusterId: identities.clusterId,
      expectedProviderId: identities.providerId,
      ifMatchVersion: 7,
    };
    const result = getVariableValues(schema, operation.variableDefinitions ?? [], { input });
    expect(result.errors).toBeUndefined();
    expect(result.coerced?.input).toEqual({ ...input, deleteData: false });
  });
});
