import { readFileSync } from "node:fs";
import path from "node:path";
import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider, useQuery } from "@apollo/client/react";
import { parse, type MessageFormatElement } from "@formatjs/icu-messageformat-parser";
import { act, render, renderHook, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NextIntlClientProvider, createTranslator } from "next-intl";
import type { ReactNode } from "react";
import { hydrateRoot } from "react-dom/client";
import { renderToString } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { GET_CLUSTER } from "@/graphql/clusters/clusters.queries";
import { locales } from "@/i18n/config";
import { CLUSTER, AGENT_ISSUED, AGENT } from "./fixtures";
import { ClusterAgentView } from "./ClusterAgent";
import { useClusterAgent } from "./use-cluster-agent";
import { ClusterSettingsClient } from "@/app/(app)/clusters/[slug]/settings/cluster-settings-client";
import type { ClusterWithHeartbeat } from "./types";

const feedback = vi.hoisted(() => ({ success: vi.fn(), warning: vi.fn(), error: vi.fn() }));
vi.mock("sonner", () => ({ toast: feedback }));
vi.mock("@/lib/permissions/use-my-permissions", () => ({
  useMyPermissions: () => ({
    granted: new Set(["cluster.manage"]),
    loading: false,
    can: () => true,
  }),
}));
vi.mock("next/navigation", () => ({
  usePathname: () => "/clusters/prod-west/settings",
  useSearchParams: () => new URLSearchParams("section=agent"),
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));
const catalogs = Object.fromEntries(
  locales.map((locale) => [
    locale,
    JSON.parse(readFileSync(path.resolve("messages", locale + ".json"), "utf8")),
  ])
);
function tFor(locale: string) {
  return createTranslator({
    locale,
    messages: catalogs[locale],
    namespace: "clusterSettings.agent",
  });
}
const issued = {
  ...AGENT_ISSUED.issued!,
  agentKey: "TEST_ONLY_ISSUED_KEY",
  heartbeatUrl: "https://test.invalid/LITERAL_HEARTBEAT_PATH",
};
const serverCluster = {
  ...CLUSTER,
  __typename: "AstroliftTenantCluster",
  organizationSlug: "LITERAL_ORG",
  region: "LITERAL_REGION",
  isActive: true,
  managedAt: null,
  lastHeartbeatAt: null,
  heartbeatStatus: "UNKNOWN",
  heartbeatAgeSeconds: null,
  createdByUsername: "LITERAL_ACTOR",
  lastBootstrapRun: null,
};
type Mode =
  | "accepted"
  | "refused"
  | "fallback"
  | "transport"
  | "refresh-failed"
  | "refused-refresh-failed"
  | "deferred"
  | "wrong-target"
  | "read-failed"
  | "read-null"
  | "read-unknown"
  | "read-deferred";
type Request = { operationName: string; variables: Record<string, unknown> };
function context(locale: string, mode: Mode = "accepted", rotated = false, activeRead = true) {
  const requests: Request[] = [];
  let reads = 0;
  let release: (() => void) | undefined;
  const fetcher = vi.fn(async (_url: unknown, init?: RequestInit) => {
    const request = JSON.parse(String(init?.body)) as Request;
    requests.push(request);
    let data;
    if (request.operationName === "GetCluster") {
      reads++;
      if (mode === "read-deferred" && request.variables.slug === "next")
        await new Promise<void>((resolve) => {
          release = resolve;
        });
      if (mode === "read-failed") throw new Error("RAW_CLUSTER_READ_ERROR");
      if (reads > 1 && (mode === "refresh-failed" || mode === "refused-refresh-failed"))
        throw new Error("RAW_CLUSTER_READ_ERROR");
      data =
        mode === "read-unknown"
          ? {}
          : {
              astroliftCluster:
                mode === "read-null"
                  ? null
                  : request.variables.slug === "next"
                    ? { ...serverCluster, id: "NEXT_CLUSTER", slug: "next" }
                    : serverCluster,
            };
    } else {
      if (mode === "deferred")
        await new Promise<void>((resolve) => {
          release = resolve;
        });
      if (mode === "transport") throw new Error("RAW_TRANSPORT_ERROR");
      const operation =
        request.operationName === "IssueClusterAgentKey"
          ? "issueClusterAgentKey"
          : "deployClusterAgent";
      const ok = !["refused", "refused-refresh-failed", "fallback"].includes(mode);
      data = {
        [operation]: {
          ok,
          errors:
            ok || mode === "fallback"
              ? []
              : [{ code: "PERMISSION_DENIED", message: "RAW_SERVER_REFUSAL", field: "clusterId" }],
          data: !ok
            ? null
            : operation === "issueClusterAgentKey"
              ? {
                  ...issued,
                  rotated,
                  clusterId: mode === "wrong-target" ? "FOREIGN_CLUSTER" : CLUSTER.id,
                }
              : {
                  __typename: "AstroliftTenantCluster",
                  id: CLUSTER.id,
                  slug: CLUSTER.slug,
                  agentProvisioned: true,
                  heartbeatStatus: "UNKNOWN",
                },
        },
      };
    }
    return new Response(JSON.stringify({ data }), {
      headers: { "Content-Type": "application/json" },
    });
  });
  const client = new ApolloClient({
    link: new HttpLink({ uri: "https://test.invalid/graphql/", fetch: fetcher as typeof fetch }),
    cache: new InMemoryCache(),
  });
  function ActiveRead() {
    useQuery(GET_CLUSTER, {
      variables: { slug: "LITERAL_CLUSTER_SLUG" },
      fetchPolicy: "network-only",
    });
    return null;
  }
  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <NextIntlClientProvider
        locale={locale}
        messages={catalogs[locale]}
        now={new Date("2026-09-30T16:00:00Z")}
        timeZone="America/Costa_Rica"
      >
        <ApolloProvider client={client}>
          {activeRead && <ActiveRead />}
          {children}
        </ApolloProvider>
      </NextIntlClientProvider>
    );
  }
  return {
    Wrapper,
    client,
    requests,
    setMode: (next: Mode) => {
      mode = next;
    },
    release: () => release?.(),
    isWaiting: () => !!release,
  };
}
function Journey() {
  return <ClusterAgentView {...useClusterAgent(CLUSTER)} />;
}
beforeEach(() => {
  vi.clearAllMocks();
});

describe.each(locales)("Cluster heartbeat agent in %s", (locale) => {
  const t = tFor(locale);
  it.each(["issue", "deploy"] as const)(
    "accepted %s refreshes the exact active cluster query",
    async (operation) => {
      const ctx = context(locale);
      render(<Journey />, { wrapper: ctx.Wrapper });
      await waitFor(() =>
        expect(ctx.requests.filter((r) => r.operationName === "GetCluster")).toHaveLength(1)
      );
      const action = screen.getByRole("button", {
        name: t(operation === "issue" ? "rotate" : "deploy"),
      });
      if (operation === "deploy") {
        action.focus();
        await userEvent.keyboard("{Enter}");
      } else await userEvent.click(action);
      await waitFor(() =>
        expect(feedback.success).toHaveBeenCalledWith(
          t(operation === "issue" ? "feedbackIssued" : "feedbackDeployed")
        )
      );
      expect(
        ctx.requests.filter((r) => r.operationName === "GetCluster").map((r) => r.variables)
      ).toEqual([{ slug: "LITERAL_CLUSTER_SLUG" }, { slug: "LITERAL_CLUSTER_SLUG" }]);
      expect(
        ctx.requests.find(
          (r) =>
            r.operationName ===
            (operation === "issue" ? "IssueClusterAgentKey" : "DeployClusterAgent")
        )?.variables
      ).toEqual({ input: { clusterId: CLUSTER.id } });
      if (operation === "issue") {
        expect(screen.getByText(issued.agentKey)).toBeTruthy();
        expect(screen.getByText((text) => text.includes(issued.heartbeatUrl))).toBeTruthy();
        await userEvent.click(screen.getByRole("button", { name: t("done") }));
        expect(screen.queryByText(issued.agentKey)).toBeNull();
      }
      expect(feedback.error).not.toHaveBeenCalled();
      expect(feedback.warning).not.toHaveBeenCalled();
    }
  );
  it.each(["issue", "deploy"] as const)("accepted %s survives a failed read", async (operation) => {
    const ctx = context(locale, "refresh-failed");
    const hook = renderHook(() => useClusterAgent(CLUSTER), { wrapper: ctx.Wrapper });
    await waitFor(() => expect(ctx.requests).toHaveLength(1));
    await act(async () => {
      await (operation === "issue"
        ? hook.result.current.onIssue()
        : hook.result.current.onDeploy());
    });
    expect(feedback.success).toHaveBeenCalledWith(
      t(operation === "issue" ? "feedbackIssued" : "feedbackDeployed")
    );
    expect(feedback.warning).toHaveBeenCalledWith(t("feedbackRefreshWarning"));
    expect(feedback.error).not.toHaveBeenCalled();
    if (operation === "issue") expect(hook.result.current.issued).toEqual(issued);
  });
  it.each(["refused", "refused-refresh-failed", "transport", "fallback"] as const)(
    "%s never announces acceptance or refreshes",
    async (mode) => {
      for (const operation of ["issue", "deploy"] as const) {
        const ctx = context(locale, mode);
        const hook = renderHook(() => useClusterAgent(CLUSTER), { wrapper: ctx.Wrapper });
        await waitFor(() => expect(ctx.requests).toHaveLength(1));
        await act(async () => {
          await (operation === "issue"
            ? hook.result.current.onIssue()
            : hook.result.current.onDeploy());
        });
        expect(feedback.error).toHaveBeenLastCalledWith(
          mode === "transport"
            ? "RAW_TRANSPORT_ERROR"
            : mode === "fallback"
              ? t(operation === "issue" ? "feedbackIssueFailed" : "feedbackDeployFailed")
              : "RAW_SERVER_REFUSAL"
        );
        expect(hook.result.current.issued).toBeNull();
        expect(ctx.requests.filter((r) => r.operationName === "GetCluster")).toHaveLength(1);
        expect(feedback.success).not.toHaveBeenCalled();
        expect(feedback.warning).not.toHaveBeenCalled();
        hook.unmount();
        ctx.client.stop();
      }
    }
  );
  it("retains an already issued key when rotation or deployment is refused", async () => {
    const ctx = context(locale);
    const hook = renderHook(() => useClusterAgent(CLUSTER), { wrapper: ctx.Wrapper });
    await waitFor(() => expect(ctx.requests).toHaveLength(1));
    await act(async () => {
      await hook.result.current.onIssue();
    });
    expect(hook.result.current.issued).toEqual(issued);
    vi.clearAllMocks();
    ctx.setMode("refused-refresh-failed");
    await act(async () => {
      await hook.result.current.onIssue();
      await hook.result.current.onDeploy();
    });
    expect(hook.result.current.issued).toEqual(issued);
    expect(feedback.error).toHaveBeenCalledTimes(2);
    expect(feedback.error).toHaveBeenLastCalledWith("RAW_SERVER_REFUSAL");
    expect(feedback.success).not.toHaveBeenCalled();
    expect(feedback.warning).not.toHaveBeenCalled();
    expect(ctx.requests.filter((r) => r.operationName === "GetCluster")).toHaveLength(2);
  });
  it("uses the rotated outcome supplied by the server", async () => {
    const ctx = context(locale, "accepted", true);
    const hook = renderHook(() => useClusterAgent(CLUSTER), { wrapper: ctx.Wrapper });
    await waitFor(() => expect(ctx.requests).toHaveLength(1));
    await act(async () => {
      await hook.result.current.onIssue();
    });
    expect(feedback.success).toHaveBeenCalledWith(t("feedbackRotated"));
  });
  it("awaits clipboard acceptance and keeps literal key/snippet available on refusal", async () => {
    const writeText = vi
      .fn()
      .mockRejectedValueOnce(new Error("RAW_CLIPBOARD_ERROR"))
      .mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    render(
      <NextIntlClientProvider locale={locale} messages={catalogs[locale]}>
        <ClusterAgentView {...AGENT_ISSUED} issued={issued} />
      </NextIntlClientProvider>
    );
    await userEvent.click(screen.getAllByRole("button", { name: t("copy") })[0]);
    expect(await screen.findByRole("alert")).toHaveTextContent(t("copyFailed"));
    expect(screen.getAllByRole("button", { name: t("copy") })).toHaveLength(2);
    expect(screen.getByText(issued.agentKey)).toBeTruthy();
    await userEvent.click(screen.getAllByRole("button", { name: t("copy") })[0]);
    await waitFor(() => expect(screen.getByRole("button", { name: t("copied") })).toBeTruthy());
    expect(screen.queryByRole("alert")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: t("copy") }));
    await waitFor(() =>
      expect(screen.getAllByRole("button", { name: t("copied") })).toHaveLength(2)
    );
    expect(writeText.mock.calls.map((args) => args[0])).toEqual([
      issued.agentKey,
      issued.agentKey,
      [
        "kubectl create secret generic astrolift-agent \\",
        "  --namespace astrolift-system \\",
        `  --from-literal=heartbeat_url='${issued.heartbeatUrl}' \\`,
        `  --from-literal=agent_key='${issued.agentKey}'`,
      ].join("\n"),
    ]);
  });
  it("changes locale without discarding a displayed credential or changing its install command", () => {
    const view = (l: string) => (
      <NextIntlClientProvider locale={l} messages={catalogs[l]}>
        <ClusterAgentView {...AGENT_ISSUED} issued={issued} />
      </NextIntlClientProvider>
    );
    const result = render(view("en"));
    const snippet = result.container.querySelector("pre")?.textContent;
    result.rerender(view(locale));
    expect(screen.getByText(t("title"))).toBeTruthy();
    expect(screen.getByText(issued.agentKey)).toBeTruthy();
    expect(result.container.querySelector("pre")?.textContent).toBe(snippet);
  });
  it("hydrates request-bound rich labels and numeric interval consistently", async () => {
    const errors: unknown[] = [];
    const element = (
      <NextIntlClientProvider
        locale={locale}
        messages={catalogs[locale]}
        now={new Date("2026-09-30T16:00:00Z")}
        timeZone="Asia/Tokyo"
      >
        <ClusterAgentView {...AGENT} heartbeatIntervalSeconds={12345} />
      </NextIntlClientProvider>
    );
    const html = renderToString(element);
    const container = document.createElement("div");
    container.innerHTML = html;
    document.body.append(container);
    const parsedHtml = container.innerHTML;
    let root: ReturnType<typeof hydrateRoot>;
    await act(async () => {
      root = hydrateRoot(container, element, { onRecoverableError: (error) => errors.push(error) });
    });
    expect(container.textContent).toContain(new Intl.NumberFormat(locale).format(12345));
    expect(container.querySelector("code")?.textContent).toBe("astrolift-system");
    expect(errors).toEqual([]);
    expect(container.innerHTML).toBe(parsedHtml);
    await act(async () => root!.unmount());
    container.remove();
  });
});

function SettingsJourney({ slug = CLUSTER.slug }: { slug?: string }) {
  return <ClusterSettingsClient slug={slug} />;
}
describe.each(locales)("Cluster settings source in %s", (locale) => {
  const sourceT = createTranslator({
    locale,
    messages: catalogs[locale],
    namespace: "clusterSettings.source",
  });
  it("shows the real failed read and retries the same target before admitting credential controls", async () => {
    const ctx = context(locale, "read-failed", false, false);
    render(<SettingsJourney />, { wrapper: ctx.Wrapper });
    expect(await screen.findByRole("alert")).toHaveTextContent("RAW_CLUSTER_READ_ERROR");
    expect(screen.queryByText(sourceT("notFound", { slug: CLUSTER.slug }))).toBeNull();
    expect(screen.queryByRole("button", { name: tFor(locale)("rotate") })).toBeNull();
    ctx.setMode("accepted");
    await userEvent.click(screen.getByRole("button", { name: sourceT("retry") }));
    expect(await screen.findByRole("button", { name: tFor(locale)("rotate") })).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
    // The agent card also reads what the install withholds (calliope-installer#447).
    expect(
      ctx.requests.filter((r) => r.operationName === "GetCluster").map((r) => r.variables)
    ).toEqual([{ slug: CLUSTER.slug }, { slug: CLUSTER.slug }]);
    expect(
      ctx.requests.every((r) => ["GetCluster", "WithheldCapabilities"].includes(r.operationName))
    ).toBe(true);
  });
  it("keeps the actual route's once-issued key visible after an accepted write and failed refresh", async () => {
    const ctx = context(locale, "accepted", false, false);
    render(<SettingsJourney />, { wrapper: ctx.Wrapper });
    const rotate = await screen.findByRole("button", { name: tFor(locale)("rotate") });
    ctx.setMode("refresh-failed");
    await userEvent.click(rotate);
    expect(await screen.findByText(issued.agentKey)).toBeTruthy();
    expect(screen.getByRole("alert")).toHaveTextContent("RAW_CLUSTER_READ_ERROR");
    expect(screen.getByRole("alert")).toHaveTextContent(sourceT("cached"));
    expect(feedback.success).toHaveBeenCalledWith(tFor(locale)("feedbackIssued"));
    expect(feedback.warning).toHaveBeenCalledWith(tFor(locale)("feedbackRefreshWarning"));
    expect(feedback.error).not.toHaveBeenCalled();
    expect(
      ctx.requests.filter((r) => r.operationName === "GetCluster").map((r) => r.variables)
    ).toEqual([{ slug: CLUSTER.slug }, { slug: CLUSTER.slug }]);
  });
  it("retains a cached current-target observation with a visible failed refresh and real retry", async () => {
    const ctx = context(locale, "accepted", false, false);
    render(<SettingsJourney />, { wrapper: ctx.Wrapper });
    expect(await screen.findByRole("button", { name: tFor(locale)("rotate") })).toBeTruthy();
    ctx.setMode("refresh-failed");
    await act(async () => {
      await ctx.client.refetchQueries({ include: ["GetCluster"] }).catch(() => {});
    });
    expect(await screen.findByRole("alert")).toHaveTextContent("RAW_CLUSTER_READ_ERROR");
    expect(screen.getByRole("alert")).toHaveTextContent(sourceT("cached"));
    expect(screen.getAllByText(CLUSTER.name).length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: tFor(locale)("rotate") })).toBeTruthy();
    ctx.setMode("accepted");
    await userEvent.click(screen.getByRole("button", { name: sourceT("retry") }));
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
    expect(
      ctx.requests.filter((r) => r.operationName === "GetCluster").map((r) => r.variables)
    ).toEqual([{ slug: CLUSTER.slug }, { slug: CLUSTER.slug }, { slug: CLUSTER.slug }]);
  });
  it.each(["read-null", "read-unknown"] as const)(
    "%s keeps confirmed not-found distinct from unavailable and sends no mutation",
    async (mode) => {
      const ctx = context(locale, mode, false, false);
      render(<SettingsJourney />, { wrapper: ctx.Wrapper });
      if (mode === "read-null")
        expect(await screen.findByText(sourceT("notFound", { slug: CLUSTER.slug }))).toBeTruthy();
      else expect(await screen.findByRole("alert")).toHaveTextContent(sourceT("unknown"));
      expect(screen.queryByRole("button", { name: tFor(locale)("rotate") })).toBeNull();
      expect(ctx.requests.every((r) => r.operationName === "GetCluster")).toBe(true);
    }
  );
});
it("target change unmounts prior credential controls while the new source waits, then uses only its confirmed identity", async () => {
  const ctx = context("de", "accepted", false, false);
  const view = render(<SettingsJourney />, { wrapper: ctx.Wrapper });
  await userEvent.click(await screen.findByRole("button", { name: tFor("de")("rotate") }));
  expect(await screen.findByText(issued.agentKey)).toBeTruthy();
  ctx.setMode("read-deferred");
  view.rerender(<SettingsJourney slug="next" />);
  await waitFor(() => expect(ctx.isWaiting()).toBe(true));
  expect(screen.queryByText(issued.agentKey)).toBeNull();
  expect(screen.queryByRole("button", { name: tFor("de")("deploy") })).toBeNull();
  expect(ctx.requests.filter((r) => r.operationName === "IssueClusterAgentKey")).toHaveLength(1);
  await act(async () => ctx.release());
  expect(await screen.findByRole("button", { name: tFor("de")("deploy") })).toBeTruthy();
  expect(screen.queryByText(issued.agentKey)).toBeNull();
  ctx.setMode("accepted");
  await userEvent.click(screen.getByRole("button", { name: tFor("de")("deploy") }));
  await waitFor(() =>
    expect(feedback.success).toHaveBeenCalledWith(tFor("de")("feedbackDeployed"))
  );
  expect(ctx.requests.find((r) => r.operationName === "DeployClusterAgent")?.variables).toEqual({
    input: { clusterId: "NEXT_CLUSTER" },
  });
});

it("waits for clipboard completion and cannot mark a different displayed credential copied", async () => {
  let resolveCopy: (() => void) | undefined;
  const writeText = vi.fn(
    () =>
      new Promise<void>((resolve) => {
        resolveCopy = resolve;
      })
  );
  Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
  const view = (key: typeof issued) => (
    <NextIntlClientProvider locale="es" messages={catalogs.es}>
      <ClusterAgentView {...AGENT_ISSUED} issued={key} />
    </NextIntlClientProvider>
  );
  const result = render(view(issued));
  await userEvent.click(screen.getAllByRole("button", { name: tFor("es")("copy") })[0]);
  expect(screen.queryByRole("button", { name: tFor("es")("copied") })).toBeNull();
  const next = { ...issued, agentKey: "TEST_ONLY_REPLACEMENT_KEY" };
  result.rerender(view(next));
  await act(async () => resolveCopy?.());
  expect(screen.getByText(next.agentKey)).toBeTruthy();
  expect(screen.queryByRole("button", { name: tFor("es")("copied") })).toBeNull();
  expect(screen.getAllByRole("button", { name: tFor("es")("copy") })).toHaveLength(2);
});
it.each(locales)(
  "%s renders a one-second interval with its genuine singular/other ICU branch",
  (locale) => {
    render(
      <NextIntlClientProvider locale={locale} messages={catalogs[locale]}>
        <ClusterAgentView {...AGENT} heartbeatIntervalSeconds={1} />
      </NextIntlClientProvider>
    );
    const expected = tFor(locale).rich("description", {
      seconds: 1,
      namespace: (chunks) => chunks,
      interval: (chunks) => chunks,
    });
    expect(document.querySelector("section p")?.textContent).toBe(String(expected));
  }
);

it("clears a key on cluster change and cannot resurrect a delayed result when returning to the old cluster", async () => {
  const ctx = context("de", "deferred");
  const hook = renderHook(({ cluster }) => useClusterAgent(cluster), {
    wrapper: ctx.Wrapper,
    initialProps: { cluster: CLUSTER },
  });
  await waitFor(() => expect(ctx.requests).toHaveLength(1));
  let pending: Promise<void>;
  act(() => {
    pending = hook.result.current.onIssue();
  });
  await waitFor(() => expect(ctx.isWaiting()).toBe(true));
  const other = { ...CLUSTER, id: "NEXT_CLUSTER", slug: "next" } as ClusterWithHeartbeat;
  hook.rerender({ cluster: other });
  hook.rerender({ cluster: CLUSTER });
  await act(async () => {
    ctx.release();
    await pending!;
  });
  expect(hook.result.current.issued).toBeNull();
});
it("does not display another cluster's key response", async () => {
  const ctx = context("fr", "wrong-target");
  const hook = renderHook(() => useClusterAgent(CLUSTER), { wrapper: ctx.Wrapper });
  await waitFor(() => expect(ctx.requests).toHaveLength(1));
  await act(async () => {
    await hook.result.current.onIssue();
  });
  expect(hook.result.current.issued).toBeNull();
  expect(feedback.error).toHaveBeenCalledWith(tFor("fr")("feedbackIssueFailed"));
  expect(feedback.success).not.toHaveBeenCalled();
});
it("hides issued material belonging to a previous cluster in the pure card", () => {
  render(
    <NextIntlClientProvider locale="en" messages={catalogs.en}>
      <ClusterAgentView {...AGENT_ISSUED} clusterId="OTHER_CLUSTER" />
    </NextIntlClientProvider>
  );
  expect(screen.queryByText(AGENT_ISSUED.issued!.agentKey)).toBeNull();
  expect(document.querySelector("pre")).toBeNull();
});
function argumentsOf(nodes: MessageFormatElement[]): string[] {
  return nodes
    .flatMap((node) => {
      if (node.type === 0) return [];
      if (node.type === 8) return ["tag:" + node.value, ...argumentsOf(node.children)];
      if (node.type === 5 || node.type === 6)
        return [
          "arg:" + node.value,
          "options:" + Object.keys(node.options).sort().join(","),
          ...Object.values(node.options).flatMap((option) => argumentsOf(option.value)),
        ];
      if (node.type === 7) return ["pound"];
      return ["arg:" + node.value + ":" + node.type];
    })
    .sort();
}
it.each(locales)(
  "%s has real copy with exact ICU/rich arguments and no copied-English leaf",
  (locale) => {
    const base = {
        ...catalogs.en.clusterSettings.agent,
        ...catalogs.en.clusterSettings.source,
      } as Record<string, string>,
      translated = {
        ...catalogs[locale].clusterSettings.agent,
        ...catalogs[locale].clusterSettings.source,
      } as Record<string, string>;
    expect(Object.keys(translated)).toEqual(Object.keys(base));
    for (const [key, message] of Object.entries(translated)) {
      expect(argumentsOf(parse(message))).toEqual(argumentsOf(parse(base[key])));
      if (locale !== "en") expect(message).not.toBe(base[key]);
    }
  }
);
