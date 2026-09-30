import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { renderToString } from "react-dom/server";
import { hydrateRoot } from "react-dom/client";
import { NextIntlClientProvider } from "next-intl";
import type { PropsWithChildren } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import en from "@/messages/en.json";
import es from "@/messages/es.json";
import fr from "@/messages/fr.json";
import de from "@/messages/de.json";
import pt from "@/messages/pt-BR.json";
import ja from "@/messages/ja.json";
import ko from "@/messages/ko.json";
import zh from "@/messages/zh-Hans.json";
import { SharedModelDetailScreen } from "./SharedModelDetailScreen";
import { sharedModelDetailProps } from "./shared-model-detail.fixtures";
import { modelObservationsProps } from "./model-observations.fixtures";
import { SharedModelClient } from "@/app/(app)/models/shared/[id]/shared-model-client";
const locales = { en, es, fr, de, "pt-BR": pt, ja, ko, "zh-Hans": zh };
const scope = vi.hoisted(() => ({ id: "org", loading: false, error: null as Error | null }));
vi.mock("@/graphql/identity/identity.hooks", () => ({
  useActiveOrg: () => ({
    org: scope.id ? { id: scope.id } : null,
    loading: scope.loading,
    error: scope.error,
  }),
}));
type Request = { operationName: string; variables: Record<string, unknown> };
let requests: Request[], transport: (request: Request) => Promise<Response>;
function response(data: Record<string, unknown>) {
  return new Response(JSON.stringify({ data }), {
    headers: { "Content-Type": "application/json" },
  });
}
function wrapper() {
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
      <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
        <ApolloProvider client={client}>{children}</ApolloProvider>
      </NextIntlClientProvider>
    );
  };
}
const model = () => ({ ...sharedModelDetailProps.model!, __typename: "ClusterModelDeployment" });
beforeEach(() => {
  scope.id = "org";
  scope.loading = false;
  scope.error = null;
  requests = [];
  transport = async (request) =>
    request.operationName === "GetModelDeploymentMetrics"
      ? response({ astroliftModelDeploymentMetrics: modelObservationsProps.metrics.data })
      : request.operationName === "GetClusterModelDensity"
        ? response({ astroliftClusterModelDensity: modelObservationsProps.density.data })
        : response({
            clusterModelDeployment: {
              ...model(),
              id: request.variables.id,
              organizationId: request.variables.organizationId,
            },
          });
});
describe("actual shared model detail", () => {
  it("reads the explicit tenant/deployment identity through the actual route client", async () => {
    render(<SharedModelClient id="shared-model" />, { wrapper: wrapper() });
    await screen.findByRole("heading", { name: "Qwen production" });
    expect(
      requests.filter((request) => request.operationName === "GetClusterModelDeployment")
    ).toHaveLength(1);
    expect(requests[0]).toMatchObject({
      operationName: "GetClusterModelDeployment",
      variables: { organizationId: "org", id: "shared-model" },
    });
    expect(screen.getByRole("link", { name: "Back to models" })).toHaveAttribute("href", "/models");
    expect(screen.getAllByText("8Gi")).toHaveLength(2);
  });
  it.each([
    { id: "foreign-model", organizationId: "org" },
    { id: "shared-model", organizationId: "foreign-org" },
  ])("refuses mismatched response identity %j", async (identity) => {
    transport = async () => response({ clusterModelDeployment: { ...model(), ...identity } });
    render(<SharedModelClient id="shared-model" />, { wrapper: wrapper() });
    await screen.findByText(en.models.shared.detail.identityMismatch);
    expect(screen.queryByText("Qwen production")).not.toBeInTheDocument();
    expect(screen.queryByText("provider-one")).not.toBeInTheDocument();
  });
  it("keeps permission failures visible rather than showing a tenant-not-found page", async () => {
    transport = async () =>
      new Response(
        JSON.stringify({
          errors: [{ message: "Permission denied", extensions: { code: "PERMISSION_DENIED" } }],
        }),
        { headers: { "Content-Type": "application/json" } }
      );
    render(<SharedModelClient id="shared-model" />, { wrapper: wrapper() });
    await screen.findByText("Permission denied");
    expect(screen.getByRole("alert")).toHaveTextContent(en.models.shared.detail.readError);
    expect(screen.queryByText(en.models.shared.detail.missing)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Refresh" })).toBeEnabled();
  });
  it("retains the last read after refresh failure and allows retry without a write", async () => {
    render(<SharedModelClient id="shared-model" />, { wrapper: wrapper() });
    await screen.findByRole("heading", { name: "Qwen production" });
    transport = async () => new Response("unavailable", { status: 503 });
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await screen.findByText(en.models.shared.detail.staleFacts);
    expect(screen.getByRole("heading", { name: "Qwen production" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Refresh" })).toBeEnabled();
    transport = async () =>
      response({ clusterModelDeployment: { ...model(), name: "Refreshed model", version: 6 } });
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await screen.findByText("Refreshed model");
    expect(
      requests.filter((request) => request.operationName === "GetClusterModelDeployment")
    ).toHaveLength(3);
    expect(
      requests.every((request) =>
        [
          "GetClusterModelDeployment",
          "GetModelDeploymentMetrics",
          "GetClusterModelDensity",
        ].includes(request.operationName)
      )
    ).toBe(true);
  });
  it("does not reuse the previous tenant's deployment while the new identity loads", async () => {
    const { rerender } = render(<SharedModelClient id="shared-model" />, { wrapper: wrapper() });
    await screen.findByRole("heading", { name: "Qwen production" });
    scope.id = "org-two";
    transport = async (request) =>
      response({
        clusterModelDeployment: {
          ...model(),
          organizationId: request.variables.organizationId,
          name: "Other tenant model",
        },
      });
    rerender(<SharedModelClient id="shared-model" />);
    expect(screen.queryByText("Qwen production")).not.toBeInTheDocument();
    await screen.findByText("Other tenant model");
    expect(
      requests.filter((request) => request.operationName === "GetClusterModelDeployment").at(-1)
        ?.variables.organizationId
    ).toBe("org-two");
  });
  it("prioritizes model facts and reveals internal identity only when technical details are opened", () => {
    render(
      <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
        <SharedModelDetailScreen {...sharedModelDetailProps} />
      </NextIntlClientProvider>
    );
    expect(screen.getByText("Qwen/Qwen3-8B")).toBeVisible();
    expect(screen.getByText("provider-one")).not.toBeVisible();
    expect(screen.getByText("operation-model-123")).not.toBeVisible();
    fireEvent.click(screen.getByText(en.models.shared.detail.technical));
    expect(screen.getByText("provider-one")).toBeVisible();
    expect(screen.getByText("operation-model-123")).toBeVisible();
  });
  it("requires recorded time and positive generation before displaying a reconciliation confirmation", () => {
    render(
      <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
        <SharedModelDetailScreen
          {...sharedModelDetailProps}
          model={{
            ...sharedModelDetailProps.model!,
            ready: true,
            readinessGeneration: 0,
            readinessObservedAt: null,
            computeMode: null,
          }}
        />
      </NextIntlClientProvider>
    );
    expect(screen.getByText(en.models.shared.detail.noConfirmation)).toBeInTheDocument();
    expect(screen.getByText(en.models.shared.detail.noLiveHealth)).toBeInTheDocument();
    expect(screen.getAllByText("Unknown").length).toBeGreaterThan(0);
    expect(screen.queryByText("GPU")).not.toBeInTheDocument();
  });
  it.each(Object.keys(locales) as (keyof typeof locales)[])(
    "renders %s stored resources and callbacks without changing identities",
    (locale) => {
      const onRetry = vi.fn(),
        copy = locales[locale].models.shared.detail;
      render(
        <NextIntlClientProvider locale={locale} messages={locales[locale]} timeZone="UTC">
          <SharedModelDetailScreen {...sharedModelDetailProps} onRetry={onRetry} />
        </NextIntlClientProvider>
      );
      expect(screen.getByRole("heading", { name: copy.desired })).toBeInTheDocument();
      expect(screen.getByRole("heading", { name: copy.applied })).toBeInTheDocument();
      expect(screen.getAllByText("8Gi")).toHaveLength(2);
      expect(screen.getByText("a".repeat(40))).toBeInTheDocument();
      expect(screen.getByText("provider-one")).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: copy.refresh }));
      expect(onRetry).toHaveBeenCalledTimes(1);
    }
  );
  it.each(["fr", "ja"] as const)(
    "cold renders and hydrates %s timestamps without timezone drift",
    async (locale) => {
      const tree = (
        <NextIntlClientProvider locale={locale} messages={locales[locale]} timeZone="UTC">
          <SharedModelDetailScreen {...sharedModelDetailProps} />
        </NextIntlClientProvider>
      );
      const container = document.createElement("div");
      container.innerHTML = renderToString(tree);
      document.body.appendChild(container);
      const before = container.textContent,
        onRecoverableError = vi.fn();
      let root: ReturnType<typeof hydrateRoot> | undefined;
      await act(async () => {
        root = hydrateRoot(container, tree, { onRecoverableError });
      });
      await waitFor(() => expect(container.textContent).toBe(before));
      expect(onRecoverableError).not.toHaveBeenCalled();
      await act(async () => root?.unmount());
      container.remove();
    }
  );
});
