import { readFileSync } from "node:fs";
import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { parse as parseIcu } from "@formatjs/icu-messageformat-parser";
import { act, render, renderHook, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  buildSchema,
  execute,
  getNamedType,
  isEnumType,
  isListType,
  isNonNullType,
  isObjectType,
  parse,
  validate,
  type GraphQLFieldResolver,
} from "graphql";
import { createTranslator, NextIntlClientProvider } from "next-intl";
import { useEffect, type ReactNode } from "react";
import { hydrateRoot } from "react-dom/client";
import { renderToString } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { locales } from "@/i18n/config";
import { TooltipProvider } from "@/components/ui/tooltip";
import { heartbeatPresentation, formatHeartbeatAge } from "@/lib/cluster-heartbeat";
import { heartbeatAge } from "@/lib/i18n/cluster-heartbeat";
import { CLUSTER as SETTINGS_CLUSTER } from "../settings/fixtures";
import { ClusterStatusBody, StatusLiveStateCard } from "./ClusterStatusScreen";
import { ClusterTabFrame } from "./ClusterTabFrame";
import { CLUSTER, LIVE_CONNECTED } from "./fixtures";
import { useClusterLiveState } from "./use-cluster-live-state";
import { useClusterBySlug } from "./use-cluster-by-slug";

const catalogs = Object.fromEntries(
  locales.map((locale) => [locale, JSON.parse(readFileSync(`messages/${locale}.json`, "utf8"))])
);
const schema = buildSchema(readFileSync("schema.graphql", "utf8"));
const ID = SETTINGS_CLUSTER.id;
const OTHER = "c0ffee00-0000-4000-8000-000000000002";
type Mode = "ok" | "failed" | "missing" | "unknown" | "partial" | "wrong-target" | "deferred";
const resolver: GraphQLFieldResolver<Record<string, unknown>, unknown> = (
  object,
  _args,
  _context,
  info
) => {
  if (object && info.fieldName in object) return object[info.fieldName];
  const type = getNamedType(info.returnType);
  const wrapped = isNonNullType(info.returnType) ? info.returnType.ofType : info.returnType;
  if (isListType(wrapped)) return [];
  if (isObjectType(type)) return {};
  if (isEnumType(type)) return type.getValues()[0].name;
  return type.name === "Boolean" ? false : ["Int", "Float"].includes(type.name) ? 0 : "";
};
function harness(locale: string, initial: Mode = "ok") {
  let mode = initial;
  const waiting: (() => void)[] = [];
  const requests: { operationName: string; variables: Record<string, unknown> }[] = [];
  const client = new ApolloClient({
    cache: new InMemoryCache(),
    link: new HttpLink({
      uri: "https://test.invalid/graphql",
      fetch: async (_url, init) => {
        const request = JSON.parse(String(init?.body));
        requests.push({ operationName: request.operationName, variables: request.variables });
        const document = parse(request.query);
        expect(validate(schema, document)).toEqual([]);
        const captured = mode;
        if (captured === "failed") throw new Error("RAW_SCOPED_READ_DIAGNOSTIC");
        if (captured === "deferred") await new Promise<void>((resolve) => waiting.push(resolve));
        const live = request.operationName === "ClusterLiveState";
        const rootValue = live
          ? {
              astroliftClusterLiveState:
                captured === "missing"
                  ? null
                  : {
                      ...LIVE_CONNECTED.state!,
                      clusterId: captured === "wrong-target" ? OTHER : request.variables.clusterId,
                    },
            }
          : {
              astroliftCluster:
                captured === "missing"
                  ? null
                  : {
                      ...SETTINGS_CLUSTER,
                      slug: captured === "wrong-target" ? "FOREIGN_SLUG" : request.variables.slug,
                    },
            };
        const result =
          captured === "unknown"
            ? { data: {} }
            : captured === "partial"
              ? {
                  data: {
                    astroliftClusterLiveState: {
                      clusterId: request.variables.clusterId,
                      status: "connected",
                    },
                  },
                }
              : await execute({
                  schema,
                  document,
                  variableValues: request.variables,
                  rootValue,
                  fieldResolver: resolver,
                });
        expect("errors" in result ? result.errors : undefined).toBeUndefined();
        return new Response(JSON.stringify(result), {
          headers: { "Content-Type": "application/json" },
        });
      },
    }),
  });
  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <NextIntlClientProvider
        locale={locale}
        messages={catalogs[locale]}
        now={new Date("2026-10-01T17:00:00Z")}
        timeZone="America/Costa_Rica"
      >
        <ApolloProvider client={client}>
          <TooltipProvider>{children}</TooltipProvider>
        </ApolloProvider>
      </NextIntlClientProvider>
    );
  }
  return {
    Wrapper,
    requests,
    mode: (next: Mode) => {
      mode = next;
    },
    release: () => {
      const resolve = waiting.shift();
      expect(resolve).toBeDefined();
      resolve!();
    },
  };
}
function copy(locale: string) {
  return createTranslator({ locale, messages: catalogs[locale], namespace: "clusterConnection" });
}
function sourceCopy(locale: string) {
  return createTranslator({
    locale,
    messages: catalogs[locale],
    namespace: "clusterSettings.source",
  });
}
function slots(mounted: (name: string) => void) {
  function Driver({ name }: { name: string }) {
    useEffect(() => {
      mounted(name);
    }, [name]);
    return <p>{name}</p>;
  }
  return {
    metrics: <Driver name="RAW_METRICS" />,
    workloads: <Driver name="RAW_WORKLOADS" />,
    liveHealth: <Driver name="RAW_HEALTH" />,
    workflows: <Driver name="RAW_WORKFLOWS" />,
    lifecycle: <p>RAW_PERSISTED_LIFECYCLE</p>,
  };
}
beforeEach(() => {
  window.localStorage.clear();
});

describe.each(locales)("cluster connection in %s", (locale) => {
  it.each(["failed", "missing", "unknown", "partial", "wrong-target"] as const)(
    "keeps an initial %s heartbeat unavailable and actually retries",
    async (mode) => {
      const h = harness(locale, mode);
      const hook = renderHook(() => useClusterLiveState(ID), { wrapper: h.Wrapper });
      await waitFor(() => expect(hook.result.current.loading).toBe(false));
      expect(hook.result.current.state).toBeNull();
      expect(hook.result.current.error).toBe(
        mode === "failed" ? "RAW_SCOPED_READ_DIAGNOSTIC" : copy(locale)("unavailable")
      );
      expect(h.requests).toEqual([
        { operationName: "ClusterLiveState", variables: { clusterId: ID } },
      ]);
      h.mode("ok");
      act(() => hook.result.current.refetch());
      await waitFor(() => {
        expect(hook.result.current.loading).toBe(false);
        expect(hook.result.current.error).toBeNull();
        expect(hook.result.current.state?.clusterId).toBe(ID);
      });
      expect(h.requests).toEqual(
        Array(2).fill({ operationName: "ClusterLiveState", variables: { clusterId: ID } })
      );
    }
  );

  it.each(["failed", "missing", "unknown", "partial", "wrong-target"] as const)(
    "does not promote a %s follow-up response from cached data",
    async (mode) => {
      const h = harness(locale);
      const hook = renderHook(() => useClusterLiveState(ID), { wrapper: h.Wrapper });
      await waitFor(() => expect(hook.result.current.state?.status).toBe("connected"));
      h.mode(mode);
      act(() => hook.result.current.refetch());
      await waitFor(() => {
        expect(hook.result.current.loading).toBe(false);
        expect(hook.result.current.error).toBeTruthy();
      });
      if (mode === "failed" || mode === "unknown")
        expect(hook.result.current.state?.clusterId).toBe(ID);
      else expect(hook.result.current.state).toBeNull();
      h.mode("ok");
      act(() => hook.result.current.refetch());
      await waitFor(() => {
        expect(hook.result.current.loading).toBe(false);
        expect(hook.result.current.error).toBeNull();
        expect(hook.result.current.state?.clusterId).toBe(ID);
      });
      expect(h.requests).toHaveLength(3);
    }
  );

  it("leaves old scoped reads unable to reveal a replaced target", async () => {
    const h = harness(locale, "deferred");
    const hook = renderHook(({ id }) => useClusterLiveState(id), {
      initialProps: { id: ID },
      wrapper: h.Wrapper,
    });
    await waitFor(() => expect(h.requests).toHaveLength(1));
    hook.rerender({ id: OTHER });
    await waitFor(() => expect(h.requests).toHaveLength(2));
    await act(async () => h.release());
    expect(hook.result.current.state).toBeNull();
    await act(async () => h.release());
    await waitFor(() => expect(hook.result.current.state?.clusterId).toBe(OTHER));
    expect(h.requests).toEqual([
      { operationName: "ClusterLiveState", variables: { clusterId: ID } },
      { operationName: "ClusterLiveState", variables: { clusterId: OTHER } },
    ]);
  });

  it.each(["failed", "missing", "unknown", "wrong-target"] as const)(
    "keeps a %s slug read scoped and distinguishes known absence",
    async (mode) => {
      const h = harness(locale, mode);
      const hook = renderHook(() => useClusterBySlug(SETTINGS_CLUSTER.slug), {
        wrapper: h.Wrapper,
      });
      await waitFor(() => expect(hook.result.current.loading).toBe(false));
      expect(hook.result.current.cluster).toBeNull();
      if (mode === "missing") expect(hook.result.current.error).toBeNull();
      else
        expect(hook.result.current.error).toBe(
          mode === "failed" ? "RAW_SCOPED_READ_DIAGNOSTIC" : sourceCopy(locale)("unknown")
        );
      h.mode("ok");
      act(() => hook.result.current.refetch());
      await waitFor(() => {
        expect(hook.result.current.loading).toBe(false);
        expect(hook.result.current.error).toBeNull();
        expect(hook.result.current.cluster?.slug).toBe(SETTINGS_CLUSTER.slug);
      });
      expect(h.requests).toEqual(
        Array(2).fill({ operationName: "GetCluster", variables: { slug: SETTINGS_CLUSTER.slug } })
      );
    }
  );

  it("suppresses driver mounts throughout a failed cached connection and permits real recovery", async () => {
    const h = harness(locale);
    const mounted = vi.fn();
    const cards = slots(mounted);
    const hook = renderHook(() => useClusterLiveState(ID), { wrapper: h.Wrapper });
    await waitFor(() => expect(hook.result.current.state?.status).toBe("connected"));
    const view = render(
      <ClusterStatusBody slug={CLUSTER.slug} {...cards} liveState={hook.result.current} />,
      { wrapper: h.Wrapper }
    );
    expect(mounted).toHaveBeenCalledTimes(4);
    h.mode("failed");
    act(() => hook.result.current.refetch());
    await waitFor(() => expect(hook.result.current.error).toBe("RAW_SCOPED_READ_DIAGNOSTIC"));
    view.rerender(
      <ClusterStatusBody slug={CLUSTER.slug} {...cards} liveState={hook.result.current} />
    );
    expect(screen.queryByText("RAW_METRICS")).not.toBeInTheDocument();
    expect(screen.queryByText(copy(locale)("connected"))).not.toBeInTheDocument();
    expect(screen.getByText(copy(locale)("cached"))).toBeInTheDocument();
    expect(screen.getByText("RAW_SCOPED_READ_DIAGNOSTIC")).toBeInTheDocument();
    expect(screen.getByText("RAW_PERSISTED_LIFECYCLE")).toBeInTheDocument();
    h.mode("ok");
    await userEvent.click(screen.getByRole("button", { name: sourceCopy(locale)("retry") }));
    await waitFor(() => {
      expect(hook.result.current.loading).toBe(false);
      expect(hook.result.current.error).toBeNull();
    });
    view.rerender(
      <ClusterStatusBody slug={CLUSTER.slug} {...cards} liveState={hook.result.current} />
    );
    expect(mounted).toHaveBeenCalledTimes(8);
    expect(screen.getByText(copy(locale)("connected"))).toBeInTheDocument();
  });

  it.each(["LITERAL_FUTURE_STATUS", "__proto__", "constructor", ""])(
    "keeps %j unknown without a no-agent or offline diagnosis",
    (status) => {
      const h = harness(locale);
      const mounted = vi.fn();
      const liveState = { ...LIVE_CONNECTED, state: { ...LIVE_CONNECTED.state!, status } };
      render(<ClusterStatusBody slug={CLUSTER.slug} {...slots(mounted)} liveState={liveState} />, {
        wrapper: h.Wrapper,
      });
      expect(mounted).not.toHaveBeenCalled();
      expect(screen.queryByText(copy(locale)("noAgentTitle"))).not.toBeInTheDocument();
      expect(screen.queryByText(copy(locale)("disconnected"))).not.toBeInTheDocument();
      expect(screen.getAllByText(status || copy(locale)("unknown")).length).toBeGreaterThan(0);
      expect(heartbeatPresentation(status).dot).toBe("muted");
    }
  );

  it.each(["connected", "degraded", "offline", "never_seen"])(
    "retains the known %s connection gate with translated labels",
    (status) => {
      const h = harness(locale);
      const mounted = vi.fn();
      render(
        <ClusterStatusBody
          slug={CLUSTER.slug}
          {...slots(mounted)}
          liveState={{ ...LIVE_CONNECTED, state: { ...LIVE_CONNECTED.state!, status } }}
        />,
        { wrapper: h.Wrapper }
      );
      expect(mounted).toHaveBeenCalledTimes(["connected", "degraded"].includes(status) ? 4 : 0);
      expect(
        screen.getAllByText(
          copy(locale)(
            status === "never_seen" ? "noAgent" : (status as "connected" | "degraded" | "offline")
          )
        ).length
      ).toBeGreaterThan(0);
    }
  );

  it.each([undefined, null, 7])(
    "keeps malformed status %s unknown without mounting drivers",
    (status) => {
      const h = harness(locale);
      const mounted = vi.fn();
      render(
        <ClusterStatusBody
          slug={CLUSTER.slug}
          {...slots(mounted)}
          liveState={{
            ...LIVE_CONNECTED,
            state: { ...LIVE_CONNECTED.state!, status } as unknown as NonNullable<
              typeof LIVE_CONNECTED.state
            >,
          }}
        />,
        { wrapper: h.Wrapper }
      );
      expect(mounted).not.toHaveBeenCalled();
      expect(screen.getAllByText(copy(locale)("unknown")).length).toBeGreaterThan(0);
      expect(screen.queryByText(copy(locale)("noAgentTitle"))).not.toBeInTheDocument();
      expect(screen.queryByText(copy(locale)("disconnected"))).not.toBeInTheDocument();
    }
  );

  it("keeps a cached connection unconfirmed during a network refresh", () => {
    const h = harness(locale);
    const mounted = vi.fn();
    render(
      <ClusterStatusBody
        slug={CLUSTER.slug}
        {...slots(mounted)}
        liveState={{ ...LIVE_CONNECTED, loading: true }}
      />,
      { wrapper: h.Wrapper }
    );
    expect(mounted).not.toHaveBeenCalled();
    expect(screen.getByText(copy(locale)("cached"))).toBeInTheDocument();
    expect(screen.getByText(copy(locale)("unconfirmed"))).toBeInTheDocument();
    expect(screen.queryByText(copy(locale)("connected"))).not.toBeInTheDocument();
    expect(screen.queryByText(copy(locale)("noAgentTitle"))).not.toBeInTheDocument();
  });

  it("retains the raw cached cluster diagnostic and translated recovery action", async () => {
    const h = harness(locale);
    const retry = vi.fn();
    render(
      <ClusterTabFrame
        slug={CLUSTER.slug}
        cluster={CLUSTER}
        loading={false}
        error="RAW_CLUSTER_READ"
        onRetry={retry}
        active="status"
      >
        <p>RAW_TAB_BODY</p>
      </ClusterTabFrame>,
      { wrapper: h.Wrapper }
    );
    expect(screen.getByText("RAW_CLUSTER_READ")).toBeInTheDocument();
    expect(screen.getByText("RAW_TAB_BODY")).toBeInTheDocument();
    expect(screen.getByText(sourceCopy(locale)("cached"))).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: sourceCopy(locale)("retry") }));
    expect(retry).toHaveBeenCalledTimes(1);
  });

  it("preserves malformed app-readiness records as unknown instead of claiming readiness", () => {
    const h = harness(locale);
    const t = copy(locale);
    render(
      <StatusLiveStateCard
        slug={CLUSTER.slug}
        {...LIVE_CONNECTED}
        state={{
          ...LIVE_CONNECTED.state!,
          appReadiness: {
            RAW_NULL: null,
            RAW_STRING: "false",
            RAW_OVER: { ready: 4, total: 3 },
            RAW_NEGATIVE: { ready: -1, total: 3 },
            RAW_VALID: { ready: 0, total: 0 },
          } as unknown as NonNullable<typeof LIVE_CONNECTED.state>["appReadiness"],
        }}
      />,
      { wrapper: h.Wrapper }
    );
    expect(screen.getAllByText(t("unknown"))).toHaveLength(4);
    expect(screen.getByText(`0/0 ${t("ready")}`)).toBeInTheDocument();
    for (const name of ["RAW_NULL", "RAW_STRING", "RAW_OVER", "RAW_NEGATIVE", "RAW_VALID"])
      expect(screen.getByText(name)).toBeInTheDocument();
  });

  it.each([
    [undefined, 3],
    [null, 3],
    ["false", 2],
    [[], 2],
    [{}, 2],
  ] as const)("keeps malformed report containers %j unknown", (report, unknownFields) => {
    const h = harness(locale);
    const view = render(
      <StatusLiveStateCard
        slug={CLUSTER.slug}
        {...LIVE_CONNECTED}
        state={
          {
            ...LIVE_CONNECTED.state!,
            appReadiness: report,
            ingressIps: report,
            agentVersion: report,
            nodeCount: -1,
            nodeReadyCount: 2,
            podTotal: undefined,
            cpuUtilization: Number.NaN,
            memoryUtilization: Number.POSITIVE_INFINITY,
          } as unknown as NonNullable<typeof LIVE_CONNECTED.state>
        }
      />,
      { wrapper: h.Wrapper }
    );
    const t = copy(locale);
    // Empty reports and literal version strings remain valid in their own fields.
    expect(screen.getAllByText(t("unknown"))).toHaveLength(unknownFields);
    if (typeof report === "string") expect(screen.getByText(report)).toBeInTheDocument();
    expect(view.container.textContent).not.toMatch(/undefined|NaN|Infinity/);
    expect(screen.getAllByText("—")).toHaveLength(4);
  });

  it("retains observed zero counts and distinguishes malformed node readiness", () => {
    const h = harness(locale);
    const view = render(
      <StatusLiveStateCard
        slug={CLUSTER.slug}
        {...LIVE_CONNECTED}
        state={{
          ...LIVE_CONNECTED.state!,
          nodeCount: 0,
          nodeReadyCount: 0,
          podTotal: 0,
          cpuUtilization: 0,
          memoryUtilization: 0,
          appReadiness: {},
          ingressIps: [],
          agentVersion: "",
        }}
      />,
      { wrapper: h.Wrapper }
    );
    expect(screen.getByText("0/0")).toBeInTheDocument();
    expect(screen.getByText("0")).toBeInTheDocument();
    expect(screen.getAllByText("0%")).toHaveLength(2);
    view.rerender(
      <StatusLiveStateCard
        slug={CLUSTER.slug}
        {...LIVE_CONNECTED}
        state={{ ...LIVE_CONNECTED.state!, nodeCount: 1, nodeReadyCount: 2, cpuUtilization: 2.5 }}
      />
    );
    expect(screen.queryByText("2/1")).not.toBeInTheDocument();
    expect(screen.getByText("—")).toBeInTheDocument();
    expect(screen.getByText("250%")).toBeInTheDocument();
  });

  it("keeps overflowing utilization unknown rather than displaying Infinity", () => {
    const h = harness(locale);
    const view = render(
      <StatusLiveStateCard
        slug={CLUSTER.slug}
        {...LIVE_CONNECTED}
        state={{
          ...LIVE_CONNECTED.state!,
          cpuUtilization: Number.MAX_VALUE,
          memoryUtilization: Number.MAX_VALUE,
        }}
      />,
      { wrapper: h.Wrapper }
    );
    expect(screen.getAllByText("—")).toHaveLength(2);
    expect(view.container.textContent).not.toContain("Infinity");
  });

  it.each([
    [0, "justNow", 0],
    [4.9, "justNow", 0],
    [5, "secondsAgo", 5],
    [59, "secondsAgo", 59],
    [60, "minutesAgo", 1],
    [3599, "minutesAgo", 59],
    [3600, "hoursAgo", 1],
    [86400, "daysAgo", 1],
  ] as const)("formats the observed age %s with locale plural rules", (age, key, count) => {
    const t = copy(locale);
    expect(heartbeatAge(age, t)).toBe(key === "justNow" ? t(key) : t(key, { count }));
  });
  it.each([null, -1, Number.NaN, Number.POSITIVE_INFINITY])(
    "does not turn invalid age %s into a recent heartbeat",
    (age) => {
      expect(heartbeatAge(age, copy(locale))).toBeNull();
      expect(formatHeartbeatAge(age)).toBeNull();
    }
  );

  it("hydrates translated snapshots without replacing literal report values", async () => {
    const h = harness(locale);
    const content = (
      <h.Wrapper>
        <StatusLiveStateCard slug={CLUSTER.slug} {...LIVE_CONNECTED} />
      </h.Wrapper>
    );
    const container = document.createElement("div");
    container.innerHTML = renderToString(content);
    document.body.append(container);
    const errors: unknown[] = [];
    let root!: ReturnType<typeof hydrateRoot>;
    await act(async () => {
      root = hydrateRoot(container, content, { onRecoverableError: (error) => errors.push(error) });
    });
    expect(errors).toEqual([]);
    expect(container.textContent).toContain(copy(locale)("title"));
    expect(container.textContent).toContain(LIVE_CONNECTED.state!.agentVersion);
    expect(container.textContent).toContain(LIVE_CONNECTED.state!.ingressIps[0]);
    await act(async () => root.unmount());
    container.remove();
  });
});

it("keeps connection keys and ICU arguments identical across eight catalogs", () => {
  function argumentsOf(message: string): string[] {
    const result = new Set<string>();
    function walk(nodes: ReturnType<typeof parseIcu>) {
      for (const node of nodes) {
        if (node.type !== 0 && "value" in node) result.add(node.value);
        if ("children" in node) walk(node.children);
        if ("options" in node) for (const option of Object.values(node.options)) walk(option.value);
      }
    }
    walk(parseIcu(message));
    return [...result].sort();
  }
  const base = catalogs.en.clusterConnection;
  expect(Object.keys(base)).toHaveLength(36);
  for (const locale of locales) {
    const entries = catalogs[locale].clusterConnection;
    expect(Object.keys(entries).sort()).toEqual(Object.keys(base).sort());
    expect(catalogs[locale].home.health.unknown).toBe(entries.unknown);
    for (const key of Object.keys(base))
      expect(argumentsOf(entries[key])).toEqual(argumentsOf(base[key]));
  }
});
