import { NextIntlClientProvider } from "next-intl";
import messages from "@/messages/en.json";
import { MockedProvider } from "@apollo/client/testing/react";
import type { MockedResponse } from "@apollo/client/testing";
import { act, renderHook, waitFor } from "@testing-library/react";
import { buildSchema, validate } from "graphql";
import { readFileSync } from "node:fs";
import { beforeEach, expect, it, vi } from "vitest";
import {
  DELETE_PIPELINE_SECRET,
  SET_PIPELINE_SECRET,
} from "@/graphql/pipelines/pipelines.mutations";
import { GET_PIPELINE, LIST_PIPELINE_SECRETS } from "@/graphql/pipelines/pipelines.queries";
import { usePipelineSecrets } from "./use-pipeline-secrets";

const toasts = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn() }));
vi.mock("sonner", () => ({ toast: toasts }));
const pipelineId = "11111111-1111-4111-8111-111111111111";
const secret = {
  id: pipelineId,
  name: "TOKEN",
  createdAt: "2026-09-01T12:00:00Z",
  updatedAt: "2026-09-01T12:00:00Z",
};
const list = { query: LIST_PIPELINE_SECRETS, variables: { pipelineId } };
const initial: MockedResponse = {
  request: list,
  result: { data: { astroliftPipelineSecrets: [secret] } },
};
const save = {
  query: SET_PIPELINE_SECRET,
  variables: { input: { pipelineId, name: "TOKEN", value: "synthetic-fixture" } },
};
const remove = {
  query: DELETE_PIPELINE_SECRET,
  variables: { input: { pipelineId, name: "TOKEN" } },
};
function setup(responses: MockedResponse[]) {
  return renderHook(() => usePipelineSecrets(pipelineId), {
    wrapper: ({ children }) => (
      <NextIntlClientProvider locale="en" messages={messages} timeZone="UTC">
        <MockedProvider mocks={[initial, ...responses]}>{children}</MockedProvider>
      </NextIntlClientProvider>
    ),
  });
}
beforeEach(() => vi.clearAllMocks());
it("validates all pipeline documents against the exported live schema", () => {
  const schema = buildSchema(readFileSync("schema.graphql", "utf8"));
  for (const document of [
    GET_PIPELINE,
    LIST_PIPELINE_SECRETS,
    SET_PIPELINE_SECRET,
    DELETE_PIPELINE_SECRET,
  ])
    expect(validate(schema, document)).toEqual([]);
});
it("treats saved-secret refresh failure as a committed write", async () => {
  const mutation = vi.fn(() => ({
    data: { setPipelineSecret: { ok: true, errors: [], data: { pipelineId, name: "TOKEN" } } },
  }));
  const { result } = setup([
    { request: save, result: mutation },
    { request: list, error: new Error("Refresh interrupted") },
  ]);
  await waitFor(() => expect(result.current.secrets).toHaveLength(1));
  let committed = false;
  await act(async () => {
    committed = await result.current.saveSecret("TOKEN", "synthetic-fixture");
  });
  expect(committed).toBe(true);
  expect(mutation).toHaveBeenCalledOnce();
  expect(toasts.success).toHaveBeenCalledOnce();
  expect(toasts.warning).toHaveBeenCalledOnce();
  expect(toasts.error).not.toHaveBeenCalled();
});
it("a refused write returns false and retries the same draft", async () => {
  const success = vi.fn(() => ({
    data: { setPipelineSecret: { ok: true, errors: [], data: { pipelineId, name: "TOKEN" } } },
  }));
  const { result } = setup([
    {
      request: save,
      result: {
        data: {
          setPipelineSecret: {
            ok: false,
            errors: [{ code: "PERMISSION_DENIED", message: "Owner grant required" }],
            data: null,
          },
        },
      },
    },
    { request: save, result: success },
    { request: list, result: { data: { astroliftPipelineSecrets: [secret] } } },
  ]);
  await waitFor(() => expect(result.current.secrets).toHaveLength(1));
  await act(async () => {
    expect(await result.current.saveSecret("TOKEN", "synthetic-fixture")).toBe(false);
  });
  expect(toasts.error).toHaveBeenCalledWith("Owner grant required");
  await act(async () => {
    expect(await result.current.saveSecret("TOKEN", "synthetic-fixture")).toBe(true);
  });
  expect(success).toHaveBeenCalledOnce();
});
it("a transport write failure returns false without announcing success", async () => {
  const { result } = setup([{ request: save, error: new Error("Network interrupted") }]);
  await waitFor(() => expect(result.current.secrets).toHaveLength(1));
  await act(async () => {
    expect(await result.current.saveSecret("TOKEN", "synthetic-fixture")).toBe(false);
  });
  expect(toasts.error).toHaveBeenCalledWith("Network interrupted");
  expect(toasts.success).not.toHaveBeenCalled();
});
it("keeps deletion rejection so the confirmation remains open", async () => {
  const { result } = setup([
    {
      request: remove,
      result: {
        data: {
          deletePipelineSecret: {
            ok: false,
            errors: [{ code: "PERMISSION_DENIED", message: "Owner grant required" }],
            data: null,
          },
        },
      },
    },
  ]);
  await waitFor(() => expect(result.current.secrets).toHaveLength(1));
  await act(async () => {
    await expect(result.current.deleteSecret(secret)).rejects.toThrow("Owner grant required");
  });
  expect(result.current.secrets).toHaveLength(1);
  expect(toasts.success).not.toHaveBeenCalled();
});
it("a committed deletion resolves even when metadata refresh fails", async () => {
  const mutation = vi.fn(() => ({
    data: { deletePipelineSecret: { ok: true, errors: [], data: { pipelineId, name: "TOKEN" } } },
  }));
  const { result } = setup([
    { request: remove, result: mutation },
    { request: list, error: new Error("Refresh interrupted") },
  ]);
  await waitFor(() => expect(result.current.secrets).toHaveLength(1));
  await act(async () => {
    await expect(result.current.deleteSecret(secret)).resolves.toBeUndefined();
  });
  expect(mutation).toHaveBeenCalledOnce();
  expect(toasts.success).toHaveBeenCalledOnce();
  expect(toasts.warning).toHaveBeenCalledOnce();
});
