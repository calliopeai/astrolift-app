import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
import { ModelObservationsPanel, observedValue } from "./ModelObservationsPanel";
import { ModelObservationsClient } from "./ModelObservationsClient";
import { modelObservationsProps, observation } from "./model-observations.fixtures";
import { sharedModelDetailProps } from "./shared-model-detail.fixtures";
import { modelObservationWindow } from "./use-model-observations";
const locales = { en, es, fr, de, "pt-BR": pt, ja, ko, "zh-Hans": zh };
const copy = en.models.shared.observations;
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
const response = (data: Record<string, unknown>) =>
  new Response(JSON.stringify({ data }), { headers: { "Content-Type": "application/json" } });
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
const validResponse = async (request: Request) =>
  request.operationName === "GetModelDeploymentMetrics"
    ? response({
        astroliftModelDeploymentMetrics: {
          ...modelObservationsProps.metrics.data!,
          start: request.variables.start,
          end: request.variables.end,
        },
      })
    : response({
        astroliftClusterModelDensity: {
          ...modelObservationsProps.density.data!,
          start: request.variables.start,
          end: request.variables.end,
        },
      });
const tile = (label: string) =>
  within(screen.getByText(label).closest('[data-slot="card"]') as HTMLElement);
beforeEach(() => {
  scope.id = "org";
  scope.loading = false;
  scope.error = null;
  requests = [];
  transport = validResponse;
});
describe("actual scoped model observations", () => {
  it("reads exact deployment, cluster and provider within a bounded 15-minute window without writes", async () => {
    render(<ModelObservationsClient model={sharedModelDetailProps.model!} />, {
      wrapper: wrapper(),
    });
    await screen.findByText(copy.metrics.prompt_tokens_per_second);
    expect(requests).toHaveLength(2);
    const metrics = requests.find(
      (request) => request.operationName === "GetModelDeploymentMetrics"
    )!;
    expect(metrics.variables).toMatchObject({
      serviceId: "shared-model",
      expectedClusterId: "cluster-one",
      expectedProviderId: "provider-one",
    });
    expect(
      Date.parse(String(metrics.variables.end)) - Date.parse(String(metrics.variables.start))
    ).toBe(900000);
    expect(
      requests.find((request) => request.operationName === "GetClusterModelDensity")?.variables
    ).toEqual({
      clusterId: "cluster-one",
      expectedProviderId: "provider-one",
      start: metrics.variables.start,
      end: metrics.variables.end,
    });
    expect(tile(copy.metrics.prompt_tokens_per_second).getByText("42")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: copy.fullCatalogue })).toHaveAttribute(
      "href",
      "/models?clusterId=cluster-one"
    );
  });
  it.each([
    { serviceId: "foreign-model" },
    { clusterId: "foreign-cluster" },
    { scope: "app_attributed" },
  ])("refuses a foreign metrics response %j", async (identity) => {
    transport = async (request) =>
      request.operationName === "GetModelDeploymentMetrics"
        ? response({
            astroliftModelDeploymentMetrics: {
              ...modelObservationsProps.metrics.data!,
              ...identity,
            },
          })
        : validResponse(request);
    render(<ModelObservationsClient model={sharedModelDetailProps.model!} />, {
      wrapper: wrapper(),
    });
    await screen.findByText(copy.identityMismatch);
    expect(screen.queryByText(copy.metrics.prompt_tokens_per_second)).not.toBeInTheDocument();
  });
  it.each([
    { clusterId: "foreign-cluster" },
    { scope: "all_cluster_models" },
    { returnedCount: 30 },
  ])("refuses mismatched or unbounded density %j", async (identity) => {
    transport = async (request) =>
      request.operationName === "GetClusterModelDensity"
        ? response({
            astroliftClusterModelDensity: { ...modelObservationsProps.density.data!, ...identity },
          })
        : validResponse(request);
    render(<ModelObservationsClient model={sharedModelDetailProps.model!} />, {
      wrapper: wrapper(),
    });
    await screen.findByText(copy.identityMismatch);
    expect(screen.queryByRole("link", { name: "Qwen production" })).not.toBeInTheDocument();
  });
  it("keeps owner denial independent from inventory-read metadata and retries only reads", async () => {
    transport = async () =>
      new Response(
        JSON.stringify({
          errors: [
            { message: "Owner permission required", extensions: { code: "PERMISSION_DENIED" } },
          ],
        }),
        { headers: { "Content-Type": "application/json" } }
      );
    render(<ModelObservationsClient model={sharedModelDetailProps.model!} />, {
      wrapper: wrapper(),
    });
    await waitFor(() => expect(screen.getAllByText("Owner permission required")).toHaveLength(2));
    expect(screen.queryByText(copy.metrics.prompt_tokens_per_second)).not.toBeInTheDocument();
    transport = validResponse;
    fireEvent.click(screen.getByRole("button", { name: copy.refresh }));
    await screen.findByText(copy.metrics.prompt_tokens_per_second);
    expect(
      requests.every((request) =>
        ["GetModelDeploymentMetrics", "GetClusterModelDensity"].includes(request.operationName)
      )
    ).toBe(true);
  });
  it("retains a visibly stale observation after failed refresh and permits a later read", async () => {
    render(<ModelObservationsClient model={sharedModelDetailProps.model!} />, {
      wrapper: wrapper(),
    });
    await screen.findByText(copy.metrics.prompt_tokens_per_second);
    transport = async () => new Response("temporarily unavailable", { status: 503 });
    fireEvent.click(screen.getByRole("button", { name: copy.refresh }));
    await waitFor(() => expect(screen.getAllByText(copy.staleRead)).toHaveLength(2));
    expect(tile(copy.metrics.prompt_tokens_per_second).getByText("42")).toBeInTheDocument();
    transport = validResponse;
    fireEvent.click(screen.getByRole("button", { name: copy.refresh }));
    await waitFor(() => expect(screen.queryByText(copy.staleRead)).not.toBeInTheDocument());
    expect(requests).toHaveLength(6);
  });
  it("does not reuse an implicit tenant density cache across organizations sharing a cluster", async () => {
    const { rerender } = render(<ModelObservationsClient model={sharedModelDetailProps.model!} />, {
      wrapper: wrapper(),
    });
    await screen.findByRole("link", { name: "Qwen production" });
    scope.id = "org-two";
    transport = async (request) =>
      request.operationName === "GetClusterModelDensity"
        ? response({
            astroliftClusterModelDensity: {
              ...modelObservationsProps.density.data!,
              items: [
                { ...modelObservationsProps.density.data!.items[0], name: "Other tenant model" },
              ],
            },
          })
        : validResponse(request);
    rerender(
      <ModelObservationsClient
        model={{ ...sharedModelDetailProps.model!, organizationId: "org-two" }}
      />
    );
    expect(screen.queryByRole("link", { name: "Qwen production" })).not.toBeInTheDocument();
    await screen.findByRole("link", { name: "Other tenant model" });
    expect(
      requests.filter((request) => request.operationName === "GetClusterModelDensity")
    ).toHaveLength(2);
  });
  it("ignores late replies after cluster/provider replacement and a return to the original selection", async () => {
    let finish: ((response: Response) => void) | undefined;
    let first = true;
    transport = async (request) => {
      if (request.operationName === "GetClusterModelDensity" && first) {
        first = false;
        return new Promise((resolve) => {
          finish = resolve;
        });
      }
      return validResponse(request);
    };
    const { rerender } = render(<ModelObservationsClient model={sharedModelDetailProps.model!} />, {
      wrapper: wrapper(),
    });
    await waitFor(() => expect(finish).toBeDefined());
    rerender(
      <ModelObservationsClient
        model={{ ...sharedModelDetailProps.model!, providerId: "provider-two" }}
      />
    );
    rerender(<ModelObservationsClient model={sharedModelDetailProps.model!} />);
    await screen.findByRole("link", { name: "Qwen production" });
    await act(async () =>
      finish?.(
        response({
          astroliftClusterModelDensity: {
            ...modelObservationsProps.density.data!,
            items: [{ ...modelObservationsProps.density.data!.items[0], name: "Late stale model" }],
          },
        })
      )
    );
    expect(screen.queryByText("Late stale model")).not.toBeInTheDocument();
    expect(
      requests.filter((request) => request.operationName === "GetClusterModelDensity").length
    ).toBeGreaterThan(1);
  });
  it("skips observations when the current tenant does not match the detail model", async () => {
    scope.id = "foreign-org";
    render(<ModelObservationsClient model={sharedModelDetailProps.model!} />, {
      wrapper: wrapper(),
    });
    await waitFor(() => expect(screen.getAllByText(copy.identityMismatch)).toHaveLength(2));
    expect(requests).toHaveLength(0);
  });
  it("preserves actual zeroes, hides unavailable values and distinguishes KV fraction from VRAM", () => {
    render(
      <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
        <ModelObservationsPanel {...modelObservationsProps} />
      </NextIntlClientProvider>
    );
    expect(tile(copy.metrics.requests_waiting).getByText("0")).toBeInTheDocument();
    expect(tile(copy.metrics.kv_cache_usage).getByText("0.25")).toBeInTheDocument();
    expect(tile(copy.metrics.kv_cache_usage).getByText(copy.kvHelp)).toBeInTheDocument();
    expect(tile(copy.metrics.vram_usage).getByText(copy.unknown)).toBeInTheDocument();
    expect(tile(copy.metrics.gpu_utilization).getByText(copy.unknown)).toBeInTheDocument();
    expect(observedValue(observation("requests_waiting", "requests", 0))).toBe(0);
    expect(observedValue(observation("requests_waiting", "requests", 3, "UNAVAILABLE"))).toBeNull();
  });
  it("discloses truncation with full server catalogue access and no browser search", () => {
    render(
      <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
        <ModelObservationsPanel
          {...modelObservationsProps}
          density={{
            ...modelObservationsProps.density,
            data: { ...modelObservationsProps.density.data!, modelCount: 31, truncated: true },
          }}
        />
      </NextIntlClientProvider>
    );
    expect(screen.getByText(copy.truncated)).toBeInTheDocument();
    expect(
      screen.getByText("Showing 1 of 31 shared models; snapshot limit 20.")
    ).toBeInTheDocument();
    expect(screen.queryByRole("searchbox")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Qwen production" })).toHaveAttribute(
      "href",
      "/models/shared/shared-model"
    );
  });
  it.each(Object.keys(locales) as (keyof typeof locales)[])(
    "renders %s observations without changing metric units, source identities or callbacks",
    (locale) => {
      const onRefresh = vi.fn(),
        localized = locales[locale].models.shared.observations;
      render(
        <NextIntlClientProvider locale={locale} messages={locales[locale]} timeZone="UTC">
          <ModelObservationsPanel {...modelObservationsProps} onRefresh={onRefresh} />
        </NextIntlClientProvider>
      );
      expect(screen.getByText(localized.metrics.prompt_tokens_per_second)).toBeInTheDocument();
      expect(
        tile(localized.metrics.prompt_tokens_per_second).getByText(
          `${localized.states.AVAILABLE} · tokens/s`
        )
      ).toBeInTheDocument();
      expect(
        tile(localized.metrics.kv_cache_usage).getByText(`${localized.states.AVAILABLE} · fraction`)
      ).toBeInTheDocument();
      expect(screen.getByRole("link", { name: localized.fullCatalogue })).toHaveAttribute(
        "href",
        "/models?clusterId=cluster-one"
      );
      fireEvent.click(screen.getByRole("button", { name: localized.refresh }));
      expect(onRefresh).toHaveBeenCalledTimes(1);
    }
  );
  it.each(["fr", "ja"] as const)(
    "hydrates %s observed timestamps and charts without drift",
    async (locale) => {
      const tree = (
        <NextIntlClientProvider locale={locale} messages={locales[locale]} timeZone="UTC">
          <ModelObservationsPanel {...modelObservationsProps} />
        </NextIntlClientProvider>
      );
      const container = document.createElement("div");
      container.innerHTML = renderToString(tree);
      document.body.appendChild(container);
      // Shared pagination initializes its selected label after mount; compare the metric section, whose dates and charts must be stable.
      const before = container.querySelector("section")?.textContent,
        onRecoverableError = vi.fn();
      let root: ReturnType<typeof hydrateRoot> | undefined;
      await act(async () => {
        root = hydrateRoot(container, tree, { onRecoverableError });
      });
      expect(container.querySelector("section")?.textContent).toBe(before);
      expect(onRecoverableError).not.toHaveBeenCalled();
      await act(async () => root?.unmount());
      container.remove();
    }
  );
  it("uses a deterministic bounded observation window", () =>
    expect(modelObservationWindow(new Date("2026-09-30T15:30:00Z"))).toEqual({
      start: "2026-09-30T15:15:00.000Z",
      end: "2026-09-30T15:30:00.000Z",
    }));
});
