import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { act, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { PropsWithChildren } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { buildSchema, validate } from "graphql";
import { readFileSync } from "node:fs";
import en from "@/messages/en.json";
import es from "@/messages/es.json";
import fr from "@/messages/fr.json";
import de from "@/messages/de.json";
import pt from "@/messages/pt-BR.json";
import ja from "@/messages/ja.json";
import ko from "@/messages/ko.json";
import zh from "@/messages/zh-Hans.json";
import * as queries from "@/graphql/models/shared-models.queries";
import type { ClusterModelFieldsFragment } from "@/graphql/__generated__/operations";
import { SharedModelsScreen } from "./SharedModelsScreen";
import { useSharedModels } from "./use-shared-models";
import { sharedModelsProps, localizedSharedModelsProps } from "./shared-models.fixtures";
import { sharedModelsVariables } from "./shared-models-list";
import { computeModeOf, gpuLabel } from "./models-list";

const locales = { en, es, fr, de, "pt-BR": pt, ja, ko, "zh-Hans": zh };
const scope = vi.hoisted(() => ({ id: "org-one", loading: false, error: null as Error | null }));
vi.mock("@/graphql/identity/identity.hooks", () => ({
  useActiveOrg: () => ({
    org: scope.id ? { id: scope.id } : null,
    loading: scope.loading,
    error: scope.error,
  }),
}));
vi.mock("@/components/list/use-list-state", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/components/list/use-list-state")>();
  return { ...actual, useListState: actual.useLocalListState };
});
type Request = { operationName: string; variables: Record<string, unknown> };
let requests: Request[], transport: (request: Request) => Promise<Response>;
function row(
  organizationId: string,
  page = 1
): ClusterModelFieldsFragment & { __typename: "ClusterModelDeployment" } {
  return {
    ...sharedModelsProps.page.rows[0],
    __typename: "ClusterModelDeployment",
    sourceKind: "huggingface",
    localArtifactId: null,
    localArtifactVersion: null,
    localManifestSha256: null,
    id: `shared-${organizationId}-${page}`,
    name: page === 1 ? "First shared model" : "Later shared model",
    organizationId,
    version: 4,
    runtimeSupported: true,
    runtimeReason: null,
    readinessGeneration: 8,
    desiredSubscriptionRevision: 2,
    appliedSubscriptionRevision: 2,
    operationId: null,
    operationStartedAt: null,
    operationCompletedAt: null,
    desiredResources: {
      cpuRequest: "2",
      memoryRequest: "8Gi",
      gpuCount: 1,
      replicas: 1,
      cpuKvCacheGiB: null,
    },
    appliedResources: {
      cpuRequest: "2",
      memoryRequest: "8Gi",
      gpuCount: 1,
      replicas: 1,
      cpuKvCacheGiB: null,
    },
  };
}
function response(data: Record<string, unknown>) {
  return new Response(JSON.stringify({ data }), {
    headers: { "Content-Type": "application/json" },
  });
}
function wrapper(locale: keyof typeof locales = "en") {
  const client = new ApolloClient({
    cache: new InMemoryCache(),
    link: new HttpLink({
      uri: "http://control-plane.test/app/gql/config/",
      fetch: async (_uri, options) => {
        const request: Request = JSON.parse(String(options?.body));
        requests.push(request);
        return transport(request);
      },
    }),
  });
  return function Provider({ children }: PropsWithChildren) {
    return (
      <NextIntlClientProvider locale={locale} messages={locales[locale]} timeZone="UTC">
        <ApolloProvider client={client}>{children}</ApolloProvider>
      </NextIntlClientProvider>
    );
  };
}
function Frame() {
  return <SharedModelsScreen {...useSharedModels()} />;
}
beforeEach(() => {
  scope.id = "org-one";
  scope.loading = false;
  scope.error = null;
  requests = [];
  transport = async (request) =>
    response({
      clusterModelDeploymentsPage: {
        items: [row(String(request.variables.organizationId), Number(request.variables.page))],
        totalCount: 60,
        page: request.variables.page,
        pageSize: request.variables.pageSize,
        nextCursor: null,
      },
    });
});
describe("actual shared model reads", () => {
  it("preserves unknown legacy compute facts instead of deriving CPU/GPU from a variant or zero devices", () => {
    expect(computeModeOf({ variant: "vllm", config: { gpu: 0 } })).toBe("unknown");
    expect(computeModeOf({ variant: "kserve", config: { gpu: 1 } })).toBe("unknown");
    expect(computeModeOf({ variant: "vllm", config: { gpu: 0, compute_mode: "cpu" } })).toBe("cpu");
    expect(computeModeOf({ variant: "bedrock", config: {} })).toBe("notApplicable");
    expect(gpuLabel({ variant: "vllm", config: {} })).toBe("Unknown");
    expect(gpuLabel({ variant: "vllm", config: { gpu: 0 } })).toBe("0 GPUs");
  });
  it("validates all read documents against the exported backend SDL", () => {
    const schema = buildSchema(readFileSync(`${process.cwd()}/schema.graphql`, "utf8"));
    for (const [name, document] of Object.entries(queries))
      if (name !== "CLUSTER_MODEL_FIELDS") expect(validate(schema, document), name).toEqual([]);
  });
  it("walks real numbered pages and keeps legacy and deploy paths visible", async () => {
    render(<Frame />, { wrapper: wrapper() });
    await screen.findByText("First shared model");
    expect(screen.getByRole("link", { name: "App, project and cloud endpoints" })).toHaveAttribute(
      "href",
      "/models/endpoints"
    );
    expect(screen.getByRole("link", { name: "Host a model" })).toHaveAttribute(
      "href",
      "/models/deploy"
    );
    fireEvent.click(screen.getByRole("button", { name: "Next page" }));
    await screen.findByText("Later shared model");
    expect(requests.at(-1)).toMatchObject({
      operationName: "ListClusterModelsPage",
      variables: { organizationId: "org-one", page: 2, pageSize: 25 },
    });
    expect(screen.getByRole("link", { name: /Later shared model/ })).toHaveAttribute(
      "href",
      "/models/shared/shared-org-one-2"
    );
  });
  it("sends model search, actual cluster, compute, status and confirmation filters before pagination", async () => {
    const { result } = renderHook(() => useSharedModels(), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.page.rows).toHaveLength(1));
    act(() => result.current.page.list.setPage(3));
    await waitFor(() => expect(requests.at(-1)?.variables.page).toBe(3));
    act(() =>
      result.current.page.list.applySearch("Qwen", {
        clusterId: "cluster-one",
        computeMode: "cpu",
        status: "active",
        ready: "false",
        subscriptionsEnabled: "true",
      })
    );
    await waitFor(() =>
      expect(requests.at(-1)?.variables).toEqual({
        organizationId: "org-one",
        search: "Qwen",
        page: 1,
        pageSize: 25,
        filter: {
          clusterId: "cluster-one",
          computeMode: "cpu",
          status: "active",
          ready: false,
          subscriptionsEnabled: true,
          deployedByMe: null,
        },
      })
    );
    expect(requests.every((request) => request.operationName === "ListClusterModelsPage")).toBe(
      true
    );
  });
  it("passes Mine as actual deployedByMe and never uses it as permission authority", () => {
    expect(
      sharedModelsVariables("org", "", { deployedByMe: "true" }, 1, 25)?.filter?.deployedByMe
    ).toBe(true);
    expect(sharedModelsVariables("org", "", { ready: "maybe" }, 1, 25)).toBeNull();
  });
  it("does not broaden an invalid Boolean filter or issue a request for it", async () => {
    const { result } = renderHook(() => useSharedModels(), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.page.rows).toHaveLength(1));
    act(() => result.current.page.list.setFilter("ready", "maybe"));
    expect(result.current.page.error?.message).toBe(en.models.shared.deployments.invalidFilter);
    expect(result.current.page.rows).toEqual([]);
    expect(requests).toHaveLength(1);
  });
  it("never renders a foreign organization response as current catalogue data", async () => {
    transport = async () =>
      response({
        clusterModelDeploymentsPage: {
          items: [row("foreign-org")],
          totalCount: 1,
          nextCursor: null,
          page: 1,
          pageSize: 25,
        },
      });
    const { result } = renderHook(() => useSharedModels(), { wrapper: wrapper() });
    await waitFor(() =>
      expect(result.current.page.error?.message).toBe(en.models.shared.deployments.unavailable)
    );
    expect(result.current.page.rows).toEqual([]);
    expect(result.current.page.totalCount).toBeNull();
  });
  it("changes organization variables and cannot reuse prior tenant rows", async () => {
    const { result, rerender } = renderHook(() => useSharedModels(), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.page.rows[0]?.id).toBe("shared-org-one-1"));
    scope.id = "org-two";
    rerender();
    expect(result.current.page.rows.some((value) => value.id === "shared-org-one-1")).toBe(false);
    await waitFor(() => expect(result.current.page.rows[0]?.id).toBe("shared-org-two-1"));
    expect(requests.at(-1)?.variables.organizationId).toBe("org-two");
  });
  it("shows transport failure and retries the read without any mutation", async () => {
    transport = async () => new Response("unavailable", { status: 503 });
    render(<Frame />, { wrapper: wrapper() });
    await screen.findByText(/503/);
    expect(screen.queryByText("No shared model deployments")).not.toBeInTheDocument();
    transport = async (request) =>
      response({
        clusterModelDeploymentsPage: {
          items: [row(String(request.variables.organizationId))],
          totalCount: 1,
          nextCursor: null,
          page: 1,
          pageSize: 25,
        },
      });
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await screen.findByText("First shared model");
    expect(requests).toHaveLength(2);
    expect(requests.every((request) => request.operationName === "ListClusterModelsPage")).toBe(
      true
    );
  });
  it("does not infer GPU compute or a live-ready result from incomplete stored facts", () => {
    render(
      <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
        <SharedModelsScreen
          page={{
            ...sharedModelsProps.page,
            rows: [
              {
                ...sharedModelsProps.page.rows[0],
                computeMode: null,
                ready: true,
                readinessObservedAt: null,
              },
            ],
          }}
        />
      </NextIntlClientProvider>
    );
    expect(screen.queryByText("Last reconciliation confirmed")).not.toBeInTheDocument();
    expect(screen.getAllByText("Unknown")).toHaveLength(2);
    expect(screen.getByText(en.models.shared.deployments.notLiveHealth)).toBeInTheDocument();
    expect(screen.queryByText("GPU")).not.toBeInTheDocument();
  });
  it.each(Object.keys(locales) as (keyof typeof locales)[])(
    "renders %s filters without changing cluster IDs or row navigation",
    (locale) => {
      const props = localizedSharedModelsProps(locales[locale].models.shared.deployments),
        setFilter = vi.fn(),
        copy = locales[locale].models.shared.deployments;
      render(
        <NextIntlClientProvider locale={locale} messages={locales[locale]} timeZone="UTC">
          <SharedModelsScreen page={{ ...props.page, list: { ...props.page.list, setFilter } }} />
        </NextIntlClientProvider>
      );
      fireEvent.click(
        screen.getByRole("button", { name: copy.filterCluster.replace("{cluster}", "Production") })
      );
      expect(setFilter).toHaveBeenCalledExactlyOnceWith("clusterId", "cluster-one");
      expect(screen.getByRole("link", { name: copy.legacy })).toHaveAttribute(
        "href",
        "/models/endpoints"
      );
      expect(screen.getByRole("link", { name: /Qwen production/ })).toHaveAttribute(
        "href",
        "/models/shared/shared-qwen"
      );
    }
  );
});
