import { readFileSync } from "node:fs";
import { buildSchema, parse, validate } from "graphql";
import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { act, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { PropsWithChildren } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import en from "@/messages/en.json";
import { SharedModelDeploymentClient } from "./SharedModelDeploymentClient";
import { useSharedModelDeployment } from "./use-shared-model-deployment";
import { sharedModelDetailProps } from "./shared-model-detail.fixtures";
import { hfCatalogueProps } from "./shared-model.fixtures";

const identity = vi.hoisted(() => ({ id: "00000000-0000-4000-8000-000000000001" }));
vi.mock("@/graphql/identity/identity.hooks", () => ({
  useActiveOrg: () => ({ org: { id: identity.id }, loading: false, error: null }),
}));
vi.mock("@/graphql/user/user.hooks", () => ({
  useMe: () => ({ user: { id: "actor-one" }, loading: false, error: null }),
}));
const clusterId = "00000000-0000-4000-8000-000000000002",
  providerId = "00000000-0000-4000-8000-000000000003",
  deploymentId = "00000000-0000-4000-8000-000000000004";
const schema = buildSchema(readFileSync("../backend/schema.graphql", "utf8"));
type Request = { query: string; operationName: string; variables: Record<string, unknown> };
let requests: Request[], transport: (request: Request) => Promise<Response>;
function response(data: Record<string, unknown>) {
  return new Response(JSON.stringify({ data }), {
    headers: { "Content-Type": "application/json" },
  });
}
function fixture(request: Request) {
  const model = {
    ...hfCatalogueProps.page.rows[0],
    gated: "NONE",
    pipelineTag: "text-generation",
    revisionSha: "a".repeat(40),
  };
  switch (request.operationName) {
    case "ListLocalModelArtifacts":
      return response({
        astroliftLocalModelArtifactsPage: {
          items: [
            {
              id: "00000000-0000-4000-8000-000000000005",
              version: 2,
              name: "Verified local weights",
              state: "verified",
              manifestSha256: "b".repeat(64),
              fileCount: 3,
              sizeBytes: "8",
            },
          ],
          nextCursor: null,
        },
      });
    case "GetModelHostingAction":
      return response({ modelHostingAction: { allowed: true, reason: null } });
    case "ListHuggingFaceConnections":
      return response({
        huggingFaceConnectionsPage: {
          items: [],
          totalCount: 0,
          page: 1,
          pageSize: 25,
          nextCursor: null,
        },
      });
    case "GetModelSourceAccess":
      return response({
        clusterModelSourceAccess: {
          accessible: true,
          reason: null,
          observedAt: "2026-09-30T15:30:00Z",
          model,
        },
      });
    case "SearchHuggingFaceModels":
      return response({
        astroliftHuggingFaceModels: {
          state: "AVAILABLE",
          source: "https://huggingface.co/api/models",
          observedAt: "2026-09-30T15:30:00Z",
          nextCursor: null,
          retryAfterSeconds: null,
          items: [model],
        },
      });
    case "GetHuggingFaceModel":
      return response({
        astroliftHuggingFaceModel: {
          state: "AVAILABLE",
          source: "https://huggingface.co/api/models",
          observedAt: "2026-09-30T15:30:00Z",
          retryAfterSeconds: null,
          model,
        },
      });
    case "ListModelPlacementClusters":
      return response({
        clusterModelPlacementClustersPage: {
          items: [
            {
              id: clusterId,
              providerId,
              name: "Production",
              slug: "production",
              region: "us-west-2",
            },
          ],
          totalCount: 21,
          page: request.variables.page,
          pageSize: request.variables.pageSize,
          nextCursor: null,
        },
      });
    case "GetClusterModelRuntimeAdmission":
      return response({
        clusterModelRuntimeAdmission: {
          eligible: true,
          reason: null,
          runtimeVersion: "0.15.1",
          architecture: "amd64",
          hardwareAdmission: "operator_declared",
        },
      });
    case "ProvisionClusterModel": {
      const input = request.variables.input as Record<string, unknown>;
      return response({
        provisionClusterModel: {
          ok: true,
          errors: [],
          data: {
            ...sharedModelDetailProps.model!,
            id: deploymentId,
            version: 3,
            organizationId: input.organizationId,
            clusterId: input.clusterId,
            providerId: input.expectedProviderId,
            modelRepo: input.localArtifactId ? "local-" + input.localArtifactId : input.modelRepo,
            sourceKind: input.localArtifactId ? "local_artifact" : "huggingface",
            localArtifactId: input.localArtifactId ?? null,
            localArtifactVersion: input.expectedArtifactVersion ?? null,
            localManifestSha256: input.localArtifactId ? "b".repeat(64) : null,
            revisionSha: input.revisionSha,
            computeMode: input.computeMode,
            name: input.name,
            subscriptionsEnabled: input.allowSubscriptions,
            status: "updating",
            ready: false,
            readinessObservedAt: null,
            readinessGeneration: null,
            operationCompletedAt: null,
            appliedResources: null,
            desiredResources: {
              cpuRequest: input.cpuRequest,
              memoryRequest: input.memoryRequest,
              gpuCount: input.gpuCount,
              cpuKvCacheGiB: input.cpuKvCacheGiB,
              replicas: 1,
            },
          },
        },
      });
    }
    default:
      throw new Error(`Unexpected operation ${request.operationName}`);
  }
}
function wrapper() {
  const client = new ApolloClient({
    cache: new InMemoryCache(),
    devtools: { enabled: false },
    link: new HttpLink({
      uri: "http://control-plane.test/app/gql/config/",
      fetch: vi.fn(async (_uri, options) => {
        const request: Request = JSON.parse(String(options?.body));
        expect(validate(schema, parse(request.query))).toEqual([]);
        requests.push(request);
        return transport(request);
      }),
    }),
  });
  return function Provider({ children }: PropsWithChildren) {
    return (
      <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
        <ApolloProvider client={client}>{children}</ApolloProvider>
      </NextIntlClientProvider>
    );
  };
}
beforeEach(() => {
  identity.id = "00000000-0000-4000-8000-000000000001";
  requests = [];
  transport = async (request) => fixture(request);
});
async function prepare(mode: "CPU" | "GPU" = "CPU") {
  fireEvent.click(
    await screen.findByRole("button", { name: hfCatalogueProps.page.rows[0].repoId })
  );
  const pin = await screen.findByRole("button", { name: en.models.shared.hosting.continue });
  await waitFor(() => expect(pin).toBeEnabled());
  fireEvent.click(pin);
  fireEvent.click(await screen.findByRole("button", { name: "Production · production" }));
  fireEvent.change(screen.getByLabelText("Deployment name"), {
    target: { value: "shared-generation" },
  });
  fireEvent.click(screen.getByLabelText(mode));
  if (mode === "GPU")
    fireEvent.change(screen.getByLabelText(en.models.shared.placement.gpuCount), {
      target: { value: "1" },
    });
  fireEvent.click(screen.getByLabelText(en.models.shared.hosting.licenseReview));
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Review deployment" })).toBeEnabled()
  );
}
function writes() {
  return requests.filter((request) => request.operationName === "ProvisionClusterModel");
}

describe("shared model create Apollo boundary", () => {
  it.each(["checking", "denied", "error"])(
    "keeps catalogue readable but Host unavailable for %s authority",
    async (kind) => {
      transport = async (request) => {
        if (request.operationName !== "GetModelHostingAction") return fixture(request);
        if (kind === "checking") return new Promise<Response>(() => {});
        if (kind === "error") throw new Error("Hosting permission read is unavailable");
        return response({
          modelHostingAction: { allowed: false, reason: "Hosting management grant is required" },
        });
      };
      render(<SharedModelDeploymentClient />, { wrapper: wrapper() });
      expect(
        await screen.findByRole("button", { name: hfCatalogueProps.page.rows[0].repoId })
      ).toBeEnabled();
      expect(screen.getByRole("button", { name: en.models.shared.hosting.host })).toBeDisabled();
      fireEvent.click(screen.getByRole("button", { name: hfCatalogueProps.page.rows[0].repoId }));
      expect(
        await screen.findByRole("button", { name: en.models.shared.hosting.continue })
      ).toBeDisabled();
      expect(
        requests.filter(
          (r) =>
            r.operationName === "ListHuggingFaceConnections" ||
            r.operationName === "ListModelPlacementClusters"
        )
      ).toHaveLength(0);
      expect(writes()).toHaveLength(0);
    }
  );
  it("Host resolves a public row without pretending catalogue access or fit is confirmed", async () => {
    render(<SharedModelDeploymentClient />, { wrapper: wrapper() });
    const host = await screen.findByRole("button", { name: en.models.shared.hosting.host });
    await waitFor(() => expect(host).toBeEnabled());
    fireEvent.click(host);
    expect(
      await screen.findByRole("button", { name: en.models.shared.hosting.continue })
    ).toBeInTheDocument();
    expect(requests.some((r) => r.operationName === "GetHuggingFaceModel")).toBe(true);
    expect(requests.some((r) => r.operationName === "GetModelSourceAccess")).toBe(false);
    expect(writes()).toHaveLength(0);
  });
  it.each(["CPU", "GPU"] as const)(
    "pins HF and admits exact %s resources before the only create write",
    async (mode) => {
      render(<SharedModelDeploymentClient />, { wrapper: wrapper() });
      await prepare(mode);
      expect(writes()).toHaveLength(0);
      fireEvent.click(screen.getByRole("button", { name: "Review deployment" }));
      expect(screen.getByRole("alertdialog")).toHaveTextContent(
        en.models.shared.placement.hardwareUnknown
      );
      fireEvent.click(screen.getByRole("button", { name: "Request deployment" }));
      expect(await screen.findByRole("link", { name: "Open deployment" })).toHaveAttribute(
        "href",
        `/models/shared/${deploymentId}`
      );
      const input = writes()[0].variables.input;
      expect(writes()).toHaveLength(1);
      expect(input).toEqual({
        organizationId: identity.id,
        clusterId,
        expectedProviderId: providerId,
        name: "shared-generation",
        modelRepo: hfCatalogueProps.page.rows[0].repoId,
        revisionSha: "a".repeat(40),
        computeMode: mode.toLowerCase(),
        cpuRequest: "2",
        memoryRequest: "8Gi",
        gpuCount: mode === "CPU" ? 0 : 1,
        cpuKvCacheGiB: null,
        allowSubscriptions: false,
        connectionId: null,
        expectedConnectionVersion: null,
        localArtifactId: null,
        expectedArtifactVersion: null,
      });
      expect(
        requests
          .filter((request) => request.operationName === "GetClusterModelRuntimeAdmission")
          .at(-1)?.variables.input
      ).toEqual(input);
      expect(screen.getByText(en.models.shared.placement.accepted)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Review deployment" })).toBeDisabled();
    }
  );
  it.each(["CPU", "GPU"] as const)(
    "hosts verified local source on %s independently of any app/HF credential",
    async (mode) => {
      render(<SharedModelDeploymentClient />, { wrapper: wrapper() });
      await screen.findByRole("button", { name: en.models.shared.hosting.host });
      fireEvent.click(
        screen.getByRole("button", { name: en.models.shared.localImport.localSource })
      );
      const select = await screen.findByRole("button", {
        name: en.models.shared.localImport.select,
      });
      await waitFor(() => expect(select).toBeEnabled());
      fireEvent.click(select);
      fireEvent.click(await screen.findByRole("button", { name: "Production · production" }));
      fireEvent.change(screen.getByLabelText("Deployment name"), {
        target: { value: "shared-local" },
      });
      fireEvent.click(screen.getByLabelText(mode));
      if (mode === "GPU")
        fireEvent.change(screen.getByLabelText(en.models.shared.placement.gpuCount), {
          target: { value: "1" },
        });
      expect(screen.getByText(en.models.shared.localImport.localLicense)).toBeVisible();
      expect(
        screen.queryByRole("link", { name: en.models.shared.hosting.openModel })
      ).not.toBeInTheDocument();
      fireEvent.click(screen.getByLabelText(en.models.shared.localImport.localLicenseReview));
      await waitFor(() =>
        expect(screen.getByRole("button", { name: "Review deployment" })).toBeEnabled()
      );
      fireEvent.click(screen.getByRole("button", { name: "Review deployment" }));
      expect(screen.getByRole("alertdialog")).toHaveTextContent("b".repeat(64));
      fireEvent.click(screen.getByRole("button", { name: "Request deployment" }));
      await screen.findByRole("link", { name: "Open deployment" });
      expect(writes()).toHaveLength(1);
      expect(writes()[0].variables.input).toMatchObject({
        organizationId: identity.id,
        clusterId,
        expectedProviderId: providerId,
        localArtifactId: "00000000-0000-4000-8000-000000000005",
        expectedArtifactVersion: 2,
        modelRepo: null,
        revisionSha: null,
        connectionId: null,
        expectedConnectionVersion: null,
        computeMode: mode.toLowerCase(),
      });
      expect(requests.some((r) => r.operationName === "GetModelSourceAccess")).toBe(false);
    }
  );
  it.each(["denial", "mixed", "missing", "foreign", "premature_ready"])(
    "retains confirmation and draft for %s without claiming success or replaying",
    async (kind) => {
      transport = async (request) => {
        if (request.operationName !== "ProvisionClusterModel") return fixture(request);
        const result = await fixture(request).json();
        const envelope = result.data.provisionClusterModel;
        if (kind === "denial" || kind === "mixed") {
          envelope.ok = kind === "mixed";
          envelope.errors = [
            {
              code: "PERMISSION_DENIED",
              message: "Current cluster access was removed",
              field: null,
              currentVersion: null,
              requestedVersion: null,
              requiresAttestation: false,
              supportedMethods: [],
            },
          ];
        }
        if (kind === "missing") envelope.data = null;
        if (kind === "foreign") envelope.data.providerId = "00000000-0000-4000-8000-000000000099";
        if (kind === "premature_ready") envelope.data.ready = true;
        return response(result.data);
      };
      render(<SharedModelDeploymentClient />, { wrapper: wrapper() });
      await prepare();
      fireEvent.click(screen.getByRole("button", { name: "Review deployment" }));
      fireEvent.click(screen.getByRole("button", { name: "Request deployment" }));
      expect(
        await screen.findByText(
          kind === "denial" || kind === "mixed"
            ? "PERMISSION_DENIED: Current cluster access was removed"
            : en.models.shared.placement.failed
        )
      ).toBeInTheDocument();
      expect(screen.getByRole("alertdialog")).toBeInTheDocument();
      expect(screen.getByLabelText("Deployment name")).toHaveValue("shared-generation");
      expect(screen.queryByRole("link", { name: "Open deployment" })).not.toBeInTheDocument();
      expect(writes()).toHaveLength(1);
    }
  );
  it("leaves unknown runtime refused and never infers admission from CPU=0 GPUs", async () => {
    transport = async (request) =>
      request.operationName === "GetClusterModelRuntimeAdmission"
        ? response({
            clusterModelRuntimeAdmission: {
              eligible: false,
              reason: "Runtime is not configured",
              runtimeVersion: null,
              architecture: null,
              hardwareAdmission: "unknown",
            },
          })
        : fixture(request);
    render(<SharedModelDeploymentClient />, { wrapper: wrapper() });
    fireEvent.click(
      await screen.findByRole("button", { name: hfCatalogueProps.page.rows[0].repoId })
    );
    const pin = await screen.findByRole("button", { name: en.models.shared.hosting.continue });
    await waitFor(() => expect(pin).toBeEnabled());
    fireEvent.click(pin);
    fireEvent.click(await screen.findByRole("button", { name: "Production · production" }));
    fireEvent.change(screen.getByLabelText("Deployment name"), { target: { value: "cpu" } });
    fireEvent.click(screen.getByLabelText("CPU"));
    expect(await screen.findByText("Runtime is not configured")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Review deployment" })).toBeDisabled();
    expect(writes()).toHaveLength(0);
  });
  it("passes later cluster pages and search to the server", async () => {
    const { result } = renderHook(useSharedModelDeployment, { wrapper: wrapper() });
    await waitFor(() => expect(result.current.hostingProps.allowed).toBe(true));
    act(() =>
      result.current.hostingProps.onManualSource({
        repoId: hfCatalogueProps.page.rows[0].repoId,
        revisionSha: "a".repeat(40),
      })
    );
    await waitFor(() => expect(result.current.clusters.rows).toHaveLength(1));
    act(() => result.current.clusters.list.setPage(3));
    await waitFor(() =>
      expect(requests.at(-1)?.variables).toMatchObject({ page: 3, pageSize: 10 })
    );
    act(() => result.current.clusters.list.applySearch("later-cluster", {}));
    await waitFor(() =>
      expect(requests.at(-1)?.variables).toMatchObject({ search: "later-cluster", page: 1 })
    );
    expect(writes()).toHaveLength(0);
  });
  it("permanently rejects a late accepted create after request A→B→A, without a second write", async () => {
    let release!: (response: Response) => void;
    transport = async (request) =>
      request.operationName === "ProvisionClusterModel"
        ? new Promise<Response>((resolve) => {
            release = resolve;
          })
        : fixture(request);
    render(<SharedModelDeploymentClient />, { wrapper: wrapper() });
    await prepare();
    fireEvent.click(screen.getByRole("button", { name: "Review deployment" }));
    fireEvent.click(screen.getByRole("button", { name: "Request deployment" }));
    await waitFor(() => expect(writes()).toHaveLength(1));
    fireEvent.change(screen.getByLabelText(en.models.shared.placement.memoryRequest), {
      target: { value: "16Gi" },
    });
    fireEvent.change(screen.getByLabelText(en.models.shared.placement.memoryRequest), {
      target: { value: "8Gi" },
    });
    await act(async () => release(fixture(writes()[0])));
    expect(screen.queryByRole("link", { name: "Open deployment" })).not.toBeInTheDocument();
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Request deployment" })).toBeDisabled();
    expect(writes()).toHaveLength(1);
  });
  it("ignores late create across organization A→B→A and clears the draft and immutable pin", async () => {
    let release!: (response: Response) => void;
    transport = async (request) =>
      request.operationName === "ProvisionClusterModel"
        ? new Promise<Response>((resolve) => {
            release = resolve;
          })
        : fixture(request);
    const { rerender } = render(<SharedModelDeploymentClient />, { wrapper: wrapper() });
    await prepare();
    fireEvent.click(screen.getByRole("button", { name: "Review deployment" }));
    fireEvent.click(screen.getByRole("button", { name: "Request deployment" }));
    await waitFor(() => expect(writes()).toHaveLength(1));
    const request = writes()[0];
    identity.id = "00000000-0000-4000-8000-000000000099";
    rerender(<SharedModelDeploymentClient />);
    identity.id = "00000000-0000-4000-8000-000000000001";
    rerender(<SharedModelDeploymentClient />);
    await act(async () => release(fixture(request)));
    expect(screen.queryByLabelText("Deployment name")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Open deployment" })).not.toBeInTheDocument();
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Review deployment" })).not.toBeInTheDocument();
    expect(writes()).toHaveLength(1);
  });
});
