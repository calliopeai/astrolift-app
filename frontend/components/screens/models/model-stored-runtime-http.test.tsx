import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider, useQuery } from "@apollo/client/react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { buildSchema, parse, validate } from "graphql";
import { readFileSync } from "node:fs";
import { createServer, type Server, type ServerResponse } from "node:http";
import type { AddressInfo } from "node:net";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import en from "@/messages/en.json";
import es from "@/messages/es.json";
import fr from "@/messages/fr.json";
import de from "@/messages/de.json";
import ja from "@/messages/ja.json";
import ko from "@/messages/ko.json";
import zh from "@/messages/zh-Hans.json";
import pt from "@/messages/pt-BR.json";
import { GET_CLUSTER_MODEL_DEPLOYMENT } from "@/graphql/models/shared-models.queries";
import type {
  ClusterModelFieldsFragment,
  GetClusterModelDeploymentQuery,
  UpdateClusterModelInput,
} from "@/graphql/__generated__/operations";
import { SharedModelManagementClient } from "./SharedModelManagementClient";
import { sharedModelDetailProps } from "./shared-model-detail.fixtures";
import { modelSettingsDraft, modelSettingsRequest } from "./shared-model-settings";
const locales = { en, es, fr, de, ja, ko, "zh-Hans": zh, "pt-BR": pt };
const identity = vi.hoisted(() => ({ org: "org", actor: "operator" }));
vi.mock("@/graphql/identity/identity.hooks", () => ({
  useActiveOrg: () => ({ org: { id: identity.org }, loading: false, error: null }),
}));
vi.mock("@/graphql/user/user.hooks", () => ({
  useMe: () => ({ user: { id: identity.actor }, loading: false, error: null }),
}));
const schema = buildSchema(readFileSync(`${process.cwd()}/schema.graphql`, "utf8"));
const original: ClusterModelFieldsFragment = {
  ...sharedModelDetailProps.model!,
  computeMode: "cpu",
  sourceKind: "local_artifact",
  revisionSha: null,
  localArtifactId: "artifact",
  localArtifactVersion: 3,
  localManifestSha256: "b".repeat(64),
  desiredResources: {
    cpuRequest: "1",
    memoryRequest: "4Gi",
    gpuCount: 0,
    replicas: 1,
    cpuKvCacheGiB: 1,
    dtype: "float32",
    maxModelLen: 512,
    maxNumSeqs: 1,
  },
  appliedResources: {
    cpuRequest: "2",
    memoryRequest: "8Gi",
    gpuCount: 0,
    replicas: 1,
    cpuKvCacheGiB: 2,
    dtype: "bfloat16",
    maxModelLen: 2048,
    maxNumSeqs: 8,
  },
};
type Request = {
  operationName: string;
  query: string;
  variables: { input?: UpdateClusterModelInput };
  response: ServerResponse;
};
let server: Server,
  client: ApolloClient,
  requests: Request[],
  model: ClusterModelFieldsFragment,
  allowed: boolean,
  admitted: boolean,
  refuse: boolean;
function reply(request: Request, data: Record<string, unknown>) {
  request.response.writeHead(200, { "content-type": "application/json" });
  request.response.end(JSON.stringify({ data }));
}
beforeEach(async () => {
  identity.org = "org";
  identity.actor = "operator";
  model = structuredClone(original);
  requests = [];
  allowed = true;
  admitted = true;
  refuse = false;
  server = createServer((incoming, response) => {
    let body = "";
    incoming.on("data", (chunk) => {
      body += String(chunk);
    });
    incoming.on("end", () => {
      const request = { ...JSON.parse(body), response } as Request;
      expect(validate(schema, parse(request.query))).toEqual([]);
      requests.push(request);
      if (request.operationName === "GetClusterModelDeployment")
        reply(request, { clusterModelDeployment: model });
      else if (request.operationName === "GetModelHostingAction")
        reply(request, {
          modelHostingAction: { allowed, reason: allowed ? null : "Operator required" },
        });
      else if (request.operationName === "GetClusterModelUpdateAdmission")
        reply(request, {
          clusterModelUpdateAdmission: {
            eligible: admitted,
            reason: admitted ? null : "The selected data type is not declared.",
            runtimeVersion: "0.15.1",
            architecture: "amd64",
            hardwareAdmission: "operator_declared",
          },
        });
      else if (request.operationName === "UpdateClusterModel") {
        const input = request.variables.input!;
        reply(request, {
          updateClusterModel: refuse
            ? {
                ok: false,
                data: null,
                errors: [
                  {
                    code: "PERMISSION_DENIED",
                    message: "Raw server refusal",
                    field: null,
                    currentVersion: null,
                    requestedVersion: null,
                    requiresAttestation: false,
                    supportedMethods: [],
                  },
                ],
              }
            : {
                ok: true,
                errors: [],
                data: {
                  ...model,
                  version: model.version + 1,
                  status: "updating",
                  operationId: "reconcile-stored-controls",
                  operationStartedAt: "2026-10-03T12:00:00Z",
                  operationCompletedAt: null,
                  ready: false,
                  desiredSubscriptionRevision: model.desiredSubscriptionRevision + 1,
                  desiredResources: {
                    ...model.desiredResources,
                    cpuRequest: input.cpuRequest,
                    memoryRequest: input.memoryRequest,
                    gpuCount: input.gpuCount,
                    cpuKvCacheGiB: input.cpuKvCacheGiB,
                    dtype: input.dtype?.toLowerCase() ?? model.desiredResources.dtype,
                    maxModelLen: input.maxModelLen ?? model.desiredResources.maxModelLen,
                    maxNumSeqs: input.maxNumSeqs ?? model.desiredResources.maxNumSeqs,
                  },
                },
              },
        });
      } else throw new Error(`Unexpected ${request.operationName}`);
    });
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  client = new ApolloClient({
    cache: new InMemoryCache(),
    link: new HttpLink({
      uri: `http://127.0.0.1:${(server.address() as AddressInfo).port}/graphql`,
    }),
    devtools: { enabled: false },
  });
});
afterEach(async () => {
  client.stop();
  for (const request of requests) if (!request.response.writableEnded) request.response.end();
  server.closeAllConnections();
  await new Promise<void>((resolve) => server.close(() => resolve()));
});
function ReadClient({ onRefresh }: { onRefresh: () => void }) {
  const { data } = useQuery<GetClusterModelDeploymentQuery>(GET_CLUSTER_MODEL_DEPLOYMENT, {
    variables: { organizationId: "org", id: original.id },
    fetchPolicy: "no-cache",
  });
  return data?.clusterModelDeployment ? (
    <SharedModelManagementClient
      model={data.clusterModelDeployment}
      blocked={false}
      onRefresh={onRefresh}
    />
  ) : null;
}
function wrapper(locale: keyof typeof locales = "en") {
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <NextIntlClientProvider locale={locale} messages={locales[locale]} timeZone="UTC">
        <ApolloProvider client={client}>{children}</ApolloProvider>
      </NextIntlClientProvider>
    );
  };
}
async function review(messages: (typeof locales)[keyof typeof locales] = en) {
  const button = screen.getByRole("button", {
    name: messages.models.shared.management.reviewUpdate,
  });
  await waitFor(() => expect(button).toBeEnabled());
  fireEvent.click(button);
}
function confirm(messages: (typeof locales)[keyof typeof locales] = en) {
  fireEvent.click(
    screen.getByRole("button", { name: messages.models.shared.management.confirmUpdate })
  );
}
describe("actual stored runtime control HTTP", () => {
  it.each(Object.entries(locales))(
    "%s reads desired values and submits reviewed controls without reconstructing its source",
    async (locale, messages) => {
      const refresh = vi.fn(() => {
        throw new Error("Read failed after commit");
      });
      render(<ReadClient onRefresh={refresh} />, {
        wrapper: wrapper(locale as keyof typeof locales),
      });
      const dtype = await screen.findByLabelText(messages.models.shared.runtimeSetup.dtype);
      expect(dtype).toHaveValue("FLOAT32");
      expect(screen.getByLabelText(messages.models.shared.runtimeSetup.maxModelLen)).toHaveValue(
        512
      );
      expect(screen.getByLabelText(messages.models.shared.runtimeSetup.maxNumSeqs)).toHaveValue(1);
      fireEvent.change(dtype, { target: { value: "FLOAT16" } });
      fireEvent.change(screen.getByLabelText(messages.models.shared.runtimeSetup.maxModelLen), {
        target: { value: "1024" },
      });
      fireEvent.change(screen.getByLabelText(messages.models.shared.runtimeSetup.maxNumSeqs), {
        target: { value: "2" },
      });
      await review(messages);
      expect(screen.getByRole("alertdialog")).toHaveTextContent("FLOAT16");
      expect(screen.getByRole("alertdialog")).toHaveTextContent("1024");
      confirm(messages);
      await screen.findByText(messages.models.shared.management.acceptedUpdate);
      expect(refresh).toHaveBeenCalledOnce();
      const input = requests.find((request) => request.operationName === "UpdateClusterModel")!
        .variables.input!;
      expect(input).toMatchObject({
        id: original.id,
        ifMatchVersion: original.version,
        expectedClusterId: original.clusterId,
        expectedProviderId: original.providerId,
        dtype: "FLOAT16",
        maxModelLen: 1024,
        maxNumSeqs: 2,
      });
      expect(
        requests
          .filter((request) => request.operationName === "GetClusterModelUpdateAdmission")
          .at(-1)!.variables.input
      ).toEqual(input);
      for (const key of [
        "modelRepo",
        "revisionSha",
        "localArtifactId",
        "connectionId",
        "config",
        "token",
        "url",
      ])
        expect(input).not.toHaveProperty(key);
      expect(requests[0].query).toContain("appliedResources");
      expect(client.cache.extract()).toEqual({});
    }
  );
  it("preserves literal unknown dtype and unrecorded controls instead of inventing defaults", async () => {
    model = {
      ...model,
      desiredResources: {
        ...model.desiredResources,
        dtype: "vendor_future_dtype",
        maxModelLen: null,
        maxNumSeqs: null,
      },
    };
    render(<ReadClient onRefresh={vi.fn()} />, { wrapper: wrapper() });
    expect(await screen.findByLabelText(en.models.shared.runtimeSetup.dtype)).toHaveValue("");
    expect(screen.getByText(/vendor_future_dtype/)).toBeVisible();
    expect(screen.getByLabelText(en.models.shared.runtimeSetup.maxModelLen)).toHaveValue(null);
    await review();
    confirm();
    await screen.findByText(en.models.shared.management.acceptedUpdate);
    expect(
      requests.find((request) => request.operationName === "UpdateClusterModel")!.variables.input
    ).toMatchObject({ dtype: null, maxModelLen: null, maxNumSeqs: null });
  });
  it("keeps an explicitly cleared control as preservation, not a new runtime default", async () => {
    render(<ReadClient onRefresh={vi.fn()} />, { wrapper: wrapper() });
    await screen.findByLabelText(en.models.shared.runtimeSetup.dtype);
    fireEvent.change(screen.getByLabelText(en.models.shared.runtimeSetup.dtype), {
      target: { value: "" },
    });
    fireEvent.change(screen.getByLabelText(en.models.shared.runtimeSetup.maxModelLen), {
      target: { value: "" },
    });
    fireEvent.change(screen.getByLabelText(en.models.shared.runtimeSetup.maxNumSeqs), {
      target: { value: "" },
    });
    await review();
    confirm();
    await screen.findByText(en.models.shared.management.acceptedUpdate);
    expect(
      requests.find((request) => request.operationName === "UpdateClusterModel")!.variables.input
    ).toMatchObject({ dtype: null, maxModelLen: null, maxNumSeqs: null });
  });
  it("retains reviewed draft and literal refusal without refreshing", async () => {
    refuse = true;
    const refresh = vi.fn();
    render(<ReadClient onRefresh={refresh} />, { wrapper: wrapper() });
    await screen.findByLabelText(en.models.shared.runtimeSetup.dtype);
    fireEvent.change(screen.getByLabelText(en.models.shared.runtimeSetup.maxModelLen), {
      target: { value: "1024" },
    });
    await review();
    confirm();
    await screen.findByText("PERMISSION_DENIED: Raw server refusal");
    expect(screen.getByLabelText(en.models.shared.runtimeSetup.maxModelLen)).toHaveValue(1024);
    expect(refresh).not.toHaveBeenCalled();
    expect(screen.getByRole("alertdialog")).toBeVisible();
  });
  it("does not review or write when actual runtime admission refuses its selected type", async () => {
    admitted = false;
    render(<ReadClient onRefresh={vi.fn()} />, { wrapper: wrapper() });
    await screen.findByText("The selected data type is not declared.");
    expect(
      screen.getByRole("button", { name: en.models.shared.management.reviewUpdate })
    ).toBeDisabled();
    expect(requests.some((request) => request.operationName === "UpdateClusterModel")).toBe(false);
  });
  it("does not read runtime admission or enable controls when hosting authority refuses", async () => {
    allowed = false;
    render(<ReadClient onRefresh={vi.fn()} />, { wrapper: wrapper() });
    await screen.findByText("Operator required");
    expect(screen.getByLabelText(en.models.shared.runtimeSetup.dtype)).toBeDisabled();
    expect(
      requests.some((request) => request.operationName === "GetClusterModelUpdateAdmission")
    ).toBe(false);
  });
  it("invalidates an open review through a runtime-field ABA", async () => {
    render(<ReadClient onRefresh={vi.fn()} />, { wrapper: wrapper() });
    await screen.findByLabelText(en.models.shared.runtimeSetup.dtype);
    await review();
    fireEvent.change(screen.getByLabelText(en.models.shared.runtimeSetup.maxNumSeqs), {
      target: { value: "2" },
    });
    await act(async () => {});
    fireEvent.change(screen.getByLabelText(en.models.shared.runtimeSetup.maxNumSeqs), {
      target: { value: "1" },
    });
    await act(async () => {});
    expect(
      screen.getByRole("button", { name: en.models.shared.management.confirmUpdate })
    ).toBeDisabled();
    confirm();
    expect(requests.some((request) => request.operationName === "UpdateClusterModel")).toBe(false);
  });
  it("resets edited controls to the new exact desired version and withdraws its previous review", async () => {
    render(<ReadClient onRefresh={vi.fn()} />, { wrapper: wrapper() });
    await screen.findByLabelText(en.models.shared.runtimeSetup.dtype);
    fireEvent.change(screen.getByLabelText(en.models.shared.runtimeSetup.maxModelLen), {
      target: { value: "1024" },
    });
    await review();
    model = {
      ...model,
      version: model.version + 1,
      desiredResources: {
        ...model.desiredResources,
        dtype: "bfloat16",
        maxModelLen: 4096,
        maxNumSeqs: 4,
      },
    };
    await act(async () => {
      await client.refetchQueries({ include: ["GetClusterModelDeployment"] });
    });
    await waitFor(() =>
      expect(screen.getByLabelText(en.models.shared.runtimeSetup.dtype)).toHaveValue("BFLOAT16")
    );
    expect(screen.getByLabelText(en.models.shared.runtimeSetup.maxModelLen)).toHaveValue(4096);
    expect(screen.getByLabelText(en.models.shared.runtimeSetup.maxNumSeqs)).toHaveValue(4);
    expect(
      screen.getByRole("button", { name: en.models.shared.management.confirmUpdate })
    ).toBeDisabled();
    confirm();
    expect(requests.some((request) => request.operationName === "UpdateClusterModel")).toBe(false);
  });
  it.each([
    ["maxModelLen", "255"],
    ["maxModelLen", "131073"],
    ["maxNumSeqs", "0"],
    ["maxNumSeqs", "4097"],
    ["maxNumSeqs", "9007199254740993"],
  ] as const)("refuses bounded invalid %s=%s", (field, value) => {
    expect(
      modelSettingsRequest(original, { ...modelSettingsDraft(original), [field]: value })
    ).toBeNull();
  });
});
