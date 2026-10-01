import { readFileSync } from "node:fs";
import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { parse as parseIcu, type MessageFormatElement } from "@formatjs/icu-messageformat-parser";
import { act, render, screen, waitFor, within } from "@testing-library/react";
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
import { useLayoutEffect, type ReactNode } from "react";
import { hydrateRoot } from "react-dom/client";
import { renderToString } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { RetentionPolicyCard } from "@/app/(app)/apps/[slug]/settings/settings-client";
import { GET_APP } from "@/graphql/registry/registry.queries";
import { locales } from "@/i18n/config";
import { PermissionsProvider } from "@/providers/PermissionsProvider";
import { RETENTION } from "./app-settings-members.fixtures";
import { RetentionPolicyView } from "./RetentionPolicy";
import { useAppSettings } from "./use-app-settings";
import {
  RETENTION_DAY_OPTIONS,
  RETENTION_DEFAULT_DAYS,
  RETENTION_SIGNALS,
  retentionSignalLabel,
  useRetentionPolicy,
  type RetentionSource,
} from "./use-retention-policy";
const state = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn() }));
vi.mock("sonner", () => ({ toast: state }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => "/apps/checkout/settings",
  useSearchParams: () => new URLSearchParams(),
}));
const catalogs = Object.fromEntries(
  locales.map((locale) => [locale, JSON.parse(readFileSync(`messages/${locale}.json`, "utf8"))])
);
const schema = buildSchema(readFileSync("schema.graphql", "utf8"));
const observed: RetentionSource = {
  id: "LITERAL_APP_GUID",
  slug: "checkout",
  version: 4,
  retentionPolicies: [{ id: "LITERAL_LOGS_POLICY_GUID", signal: "logs", retentionDays: 14 }],
};
const other: RetentionSource = {
  ...observed,
  id: "LITERAL_SECOND_GUID",
  slug: "second",
  retentionPolicies: [],
};
const fieldResolver: GraphQLFieldResolver<Record<string, unknown>, unknown> = (
  source,
  _args,
  _ctx,
  info
) => {
  if (source && info.fieldName in source) return source[info.fieldName];
  const type = getNamedType(info.returnType);
  const wrapped = isNonNullType(info.returnType) ? info.returnType.ofType : info.returnType;
  if (isListType(wrapped)) return [];
  if (isObjectType(type)) return {};
  if (isEnumType(type)) return type.getValues()[0].name;
  return type.name === "Boolean" ? false : ["Int", "Float"].includes(type.name) ? 0 : "";
};
type Mode =
  | "accepted"
  | "refused"
  | "fallback"
  | "transport"
  | "refresh-failed"
  | "refused-refresh-failed"
  | "missing"
  | "deferred"
  | "read-failed";
function context(locale: string, initialMode: Mode = "accepted") {
  let mode = initialMode;
  const snapshots = new Map<string, RetentionSource | null>([
    [observed.slug, { ...observed }],
    [other.slug, { ...other }],
  ]);
  const requests: { operationName: string; variables: Record<string, unknown> }[] = [];
  const readCounts = new Map<string, number>();
  const held: { slug: string; release: () => void }[] = [];
  const fetcher = async (_url: unknown, init?: RequestInit) => {
    const request = JSON.parse(String(init?.body));
    requests.push({ operationName: request.operationName, variables: request.variables });
    const document = parse(request.query);
    expect(validate(schema, document)).toEqual([]);
    const requestMode = mode;
    let root;
    if (request.operationName === "GetApp") {
      if (mode === "read-failed") throw new Error("RAW_INITIAL_READ_DIAGNOSTIC");
      const slug = String(request.variables.slug);
      const count = (readCounts.get(slug) ?? 0) + 1;
      readCounts.set(slug, count);
      if (count > 1 && ["refresh-failed", "refused-refresh-failed"].includes(mode))
        throw new Error("RAW_READ_DIAGNOSTIC");
      root = { astroliftApp: snapshots.get(slug) ?? null };
    } else if (request.operationName === "SetRetentionPolicy") {
      if (mode === "transport") throw new Error("RAW_TRANSPORT_DIAGNOSTIC");
      if (mode === "missing")
        return new Response(JSON.stringify({ data: null }), {
          headers: { "Content-Type": "application/json" },
        });
      const input = request.variables.input;
      const initial = snapshots.get(input.appSlug);
      if (mode === "deferred")
        await new Promise<void>((release) => held.push({ slug: input.appSlug, release }));
      const ok = !["refused", "fallback", "refused-refresh-failed"].includes(requestMode);
      const policy = {
        id: "LITERAL_POLICY_" + input.signal,
        signal: input.signal,
        retentionDays: input.retentionDays,
      };
      const current = snapshots.get(input.appSlug);
      if (ok && initial && current)
        snapshots.set(input.appSlug, {
          ...current,
          retentionPolicies: [
            ...current.retentionPolicies.filter((p) => p.signal !== input.signal),
            policy,
          ],
        });
      root = {
        setRetentionPolicy: {
          ok,
          errors: ["refused", "refused-refresh-failed"].includes(requestMode)
            ? [{ code: "PERMISSION_DENIED", message: "RAW_SERVER_REFUSAL", field: "signal" }]
            : [],
          data: ok ? policy : null,
        },
      };
    } else throw new Error("Unexpected operation " + request.operationName);
    const response = await execute({
      schema,
      document,
      variableValues: request.variables,
      rootValue: root,
      fieldResolver,
    });
    expect(response.errors).toBeUndefined();
    return new Response(JSON.stringify(response), {
      headers: { "Content-Type": "application/json" },
    });
  };
  const client = new ApolloClient({
    link: new HttpLink({ uri: "https://test.invalid/graphql/", fetch: fetcher as typeof fetch }),
    cache: new InMemoryCache(),
  });
  const t = createTranslator({
    locale,
    messages: catalogs[locale],
    namespace: "apps.settings.retentionFlow",
  });
  function Wrapper({
    children,
    allowed = true,
    loading = false,
  }: {
    children: ReactNode;
    allowed?: boolean;
    loading?: boolean;
  }) {
    return (
      <NextIntlClientProvider
        locale={locale}
        messages={catalogs[locale]}
        timeZone="America/Costa_Rica"
        now={new Date("2026-09-30T12:00:00Z")}
      >
        <ApolloProvider client={client}>
          <PermissionsProvider value={{ granted: new Set(allowed ? ["app.update"] : []), loading }}>
            {children}
          </PermissionsProvider>
        </ApolloProvider>
      </NextIntlClientProvider>
    );
  }
  return {
    client,
    Wrapper,
    t,
    requests,
    mode: (next: Mode) => {
      mode = next;
    },
    snapshot: (slug: string, source: RetentionSource | null) => snapshots.set(slug, source),
    reads: (slug = "checkout") => readCounts.get(slug) ?? 0,
    waiting: () => held.length,
    release: (index = 0) => held[index]?.release(),
    refresh: () => client.refetchQueries({ include: [GET_APP] }),
  };
}
function Card({ slug = "checkout" }: { slug?: string }) {
  const { app } = useAppSettings(slug);
  return app ? <RetentionPolicyCard app={app} /> : null;
}
async function choose(signal: string, t: ReturnType<typeof context>["t"], days = 60) {
  await userEvent.click(
    await screen.findByRole("combobox", {
      name: t("selectorLabel", { signal: retentionSignalLabel(signal, t) }),
    })
  );
  await userEvent.click(await screen.findByRole("option", { name: t("days", { days }) }));
}
beforeEach(() => {
  vi.clearAllMocks();
});
describe("actual RetentionPolicyCard / GetApp / HttpLink", () => {
  it.each(
    locales.flatMap((locale) =>
      RETENTION_SIGNALS.flatMap(({ signal }) =>
        (
          [
            "accepted",
            "refused",
            "fallback",
            "transport",
            "refresh-failed",
            "refused-refresh-failed",
            "missing",
          ] as const
        ).map((mode) => ({ locale, signal, mode }))
      )
    )
  )("$locale $signal $mode", async ({ locale, signal, mode }) => {
    const c = context(locale, mode);
    render(
      <c.Wrapper>
        <Card />
      </c.Wrapper>
    );
    await choose(signal, c.t);
    const accepted = ["accepted", "refresh-failed"].includes(mode);
    await waitFor(() => expect(accepted ? state.success : state.error).toHaveBeenCalled());
    expect(c.requests.filter((r) => r.operationName === "SetRetentionPolicy")).toEqual([
      {
        operationName: "SetRetentionPolicy",
        variables: { input: { appSlug: "checkout", signal, retentionDays: 60 } },
      },
    ]);
    if (accepted) {
      expect(state.success).toHaveBeenCalledWith(
        c.t("saveAccepted", {
          slug: "checkout",
          signal: retentionSignalLabel(signal, c.t),
          days: 60,
        })
      );
      expect(c.reads()).toBe(2);
      expect(
        c.requests.filter((r) => r.operationName === "GetApp").map((r) => r.variables)
      ).toEqual([
        { slug: "checkout", includeDrift: false },
        { slug: "checkout", includeDrift: false },
      ]);
      if (mode === "refresh-failed")
        expect(state.warning).toHaveBeenCalledWith(c.t("refreshWarning"));
      else expect(state.warning).not.toHaveBeenCalled();
      expect(state.error).not.toHaveBeenCalled();
    } else {
      expect(c.reads()).toBe(1);
      expect(state.success).not.toHaveBeenCalled();
      expect(state.warning).not.toHaveBeenCalled();
      expect(state.error).toHaveBeenCalledWith(
        ["refused", "refused-refresh-failed"].includes(mode)
          ? "RAW_SERVER_REFUSAL"
          : mode === "transport"
            ? "RAW_TRANSPORT_DIAGNOSTIC"
            : c.t(mode === "missing" ? "noResponse" : "saveFailed")
      );
      expect(
        screen.getByRole("combobox", {
          name: c.t("selectorLabel", { signal: retentionSignalLabel(signal, c.t) }),
        })
      ).toHaveTextContent(c.t("days", { days: signal === "logs" ? 14 : RETENTION_DEFAULT_DAYS }));
    }
    c.client.stop();
  });
  it.each(locales)(
    "%s retries a refused save by keyboard without inventing a saved value",
    async (locale) => {
      const c = context(locale, "refused");
      render(
        <c.Wrapper>
          <Card />
        </c.Wrapper>
      );
      await choose("logs", c.t);
      await waitFor(() => expect(state.error).toHaveBeenCalledWith("RAW_SERVER_REFUSAL"));
      c.mode("accepted");
      const picker = screen.getByRole("combobox", {
        name: c.t("selectorLabel", { signal: retentionSignalLabel("logs", c.t) }),
      });
      picker.focus();
      await userEvent.keyboard("{Enter}");
      const option = await screen.findByRole("option", { name: c.t("days", { days: 90 }) });
      option.focus();
      await userEvent.keyboard("{Enter}");
      await waitFor(() =>
        expect(state.success).toHaveBeenCalledWith(
          c.t("saveAccepted", {
            slug: "checkout",
            signal: retentionSignalLabel("logs", c.t),
            days: 90,
          })
        )
      );
      expect(c.reads()).toBe(2);
      expect(
        c.requests.filter((r) => r.operationName === "SetRetentionPolicy").map((r) => r.variables)
      ).toEqual([
        { input: { appSlug: "checkout", signal: "logs", retentionDays: 60 } },
        { input: { appSlug: "checkout", signal: "logs", retentionDays: 90 } },
      ]);
      c.client.stop();
    }
  );
  it.each(locales)(
    "%s source replacement closes an open selector; permission withdrawal removes writes",
    async (locale) => {
      const c = context(locale);
      const view = render(
        <c.Wrapper>
          <Card />
        </c.Wrapper>
      );
      await userEvent.click(
        await screen.findByRole("combobox", {
          name: c.t("selectorLabel", { signal: retentionSignalLabel("logs", c.t) }),
        })
      );
      expect(screen.getByRole("listbox")).toBeInTheDocument();
      c.snapshot("checkout", { ...observed, id: "REPLACED_GUID" });
      await act(async () => {
        await c.refresh();
      });
      expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
      view.rerender(
        <c.Wrapper allowed={false}>
          <Card />
        </c.Wrapper>
      );
      expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
      expect(c.requests.filter((r) => r.operationName === "SetRetentionPolicy")).toHaveLength(0);
      c.client.stop();
    }
  );
  it.each(["identity", "policy", "permission", "withdrawal"])(
    "%s ABA refuses a stored old callback",
    async (kind) => {
      const c = context("fr");
      let old: ((signal: string, raw: string) => Promise<void>) | undefined;
      function Capture({ source }: { source: RetentionSource | null }) {
        const h = useRetentionPolicy("checkout", source);
        old ??= h.onChange;
        return null;
      }
      const view = render(
        <c.Wrapper>
          <Capture source={observed} />
        </c.Wrapper>
      );
      const changed =
        kind === "withdrawal"
          ? null
          : {
              ...observed,
              ...(kind === "identity" ? { id: "OTHER_GUID" } : { retentionPolicies: [] }),
            };
      view.rerender(
        <c.Wrapper allowed={kind !== "permission"}>
          <Capture source={changed} />
        </c.Wrapper>
      );
      view.rerender(
        <c.Wrapper>
          <Capture source={observed} />
        </c.Wrapper>
      );
      await act(async () => {
        await old!("logs", "60");
      });
      expect(c.requests).toHaveLength(0);
      expect(state.error).toHaveBeenCalledWith(c.t("sourceChanged"));
      c.client.stop();
    }
  );
  it("late app A completion cannot clear app B's current saving state", async () => {
    const c = context("en", "deferred");
    let latest: ReturnType<typeof useRetentionPolicy> | undefined;
    function Capture({ source }: { source: RetentionSource }) {
      useAppSettings(source.slug);
      const h = useRetentionPolicy(source.slug, source);
      useLayoutEffect(() => {
        latest = h;
      }, [h]);
      return <output>{String(!!h.saving.logs)}</output>;
    }
    const view = render(
      <c.Wrapper>
        <Capture source={observed} />
      </c.Wrapper>
    );
    await waitFor(() => expect(c.reads()).toBe(1));
    await act(async () => {
      void latest!.onChange("logs", "60");
    });
    await waitFor(() => expect(c.waiting()).toBe(1));
    view.rerender(
      <c.Wrapper>
        <Capture source={other} />
      </c.Wrapper>
    );
    await waitFor(() => expect(c.reads("second")).toBe(1));
    expect(screen.getByRole("status")).toHaveTextContent("false");
    await act(async () => {
      void latest!.onChange("logs", "90");
    });
    await waitFor(() => expect(c.waiting()).toBe(2));
    expect(screen.getByRole("status")).toHaveTextContent("true");
    await act(async () => {
      c.release(0);
    });
    await waitFor(() =>
      expect(state.success).toHaveBeenCalledWith(
        c.t("saveAccepted", {
          slug: "checkout",
          signal: retentionSignalLabel("logs", c.t),
          days: 60,
        })
      )
    );
    expect(screen.getByRole("status")).toHaveTextContent("true");
    expect(c.reads("second")).toBe(1);
    await act(async () => {
      c.release(1);
    });
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("false"));
    c.client.stop();
  });
  it("same-target cached refresh failure retains a selectable recorded observation", async () => {
    const c = context("fr");
    render(
      <c.Wrapper>
        <Card />
      </c.Wrapper>
    );
    await screen.findByRole("combobox", {
      name: c.t("selectorLabel", { signal: retentionSignalLabel("logs", c.t) }),
    });
    c.mode("refresh-failed");
    await act(async () => {
      await c.refresh().catch(() => {});
    });
    const reads = c.reads();
    c.mode("refused");
    await choose("logs", c.t);
    await waitFor(() => expect(state.error).toHaveBeenCalledWith("RAW_SERVER_REFUSAL"));
    expect(c.reads()).toBe(reads);
    c.client.stop();
  });
});
function leafMessages(value: Record<string, unknown>, prefix = ""): Record<string, string> {
  return Object.fromEntries(
    Object.entries(value).flatMap(([key, child]) =>
      typeof child === "string"
        ? [[prefix + key, child]]
        : Object.entries(leafMessages(child as Record<string, unknown>, prefix + key + "."))
    )
  );
}
function structure(nodes: MessageFormatElement[]): unknown[] {
  return nodes
    .flatMap((node): unknown[] =>
      node.type === 0 || node.type === 7
        ? []
        : node.type === 6 || node.type === 5
          ? [
              {
                type: node.type,
                value: node.value,
                options: Object.fromEntries(
                  Object.entries(node.options).map(([key, option]) => [
                    key,
                    structure(option.value),
                  ])
                ),
              },
            ]
          : node.type === 8
            ? [{ type: node.type, value: node.value, children: structure(node.children) }]
            : [{ type: node.type, value: node.value }]
    )
    .sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b)));
}
describe("retention presentation contracts", () => {
  it.each(locales)("%s genuine ICU/plural and unknown literal values", (locale) => {
    const c = context(locale);
    const leaves = leafMessages(catalogs[locale].apps.settings.retentionFlow);
    const english = leafMessages(catalogs.en.apps.settings.retentionFlow);
    expect(Object.keys(leaves).sort()).toEqual(Object.keys(english).sort());
    for (const [key, value] of Object.entries(leaves)) {
      expect(structure(parseIcu(value))).toEqual(structure(parseIcu(english[key])));
      expect(c.t(key, { signal: "LITERAL_SIGNAL", slug: "LITERAL_SLUG", days: 1 })).toBeTruthy();
    }
    for (const signal of ["future.signal", "LOGS", "__proto__", "constructor"])
      expect(retentionSignalLabel(signal, c.t)).toBe(signal);
    expect(
      c.t("saveAccepted", { signal: "LITERAL_SIGNAL", slug: "LITERAL_SLUG", days: 14 })
    ).toContain("LITERAL_SLUG");
    expect(
      c.t("saveAccepted", { signal: "LITERAL_SIGNAL", slug: "LITERAL_SLUG", days: 14 })
    ).toContain("LITERAL_SIGNAL");
    if (locale !== "en") expect(leaves.title).not.toBe(english.title);
    c.client.stop();
  });
  it.each(locales)(
    "%s preserves recorded custom periods, defaults and request-local hydration",
    async (locale) => {
      const c = context(locale);
      const errors = vi.fn();
      const element = (
        <c.Wrapper>
          <RetentionPolicyView
            {...RETENTION}
            policies={[{ id: "POLICY_GUID", signal: "logs", retentionDays: 45 }]}
          />
        </c.Wrapper>
      );
      const host = document.createElement("div");
      host.innerHTML = renderToString(element);
      document.body.append(host);
      let root: ReturnType<typeof hydrateRoot>;
      await act(async () => {
        root = hydrateRoot(host, element, { onRecoverableError: errors });
      });
      expect(
        within(host).getByRole("combobox", {
          name: c.t("selectorLabel", { signal: retentionSignalLabel("logs", c.t) }),
        })
      ).toHaveTextContent(c.t("days", { days: 45 }));
      expect(within(host).getAllByText(c.t("platformDefault"))).toHaveLength(3);
      expect(errors).not.toHaveBeenCalled();
      await act(async () => {
        root!.unmount();
      });
      host.remove();
      c.client.stop();
    }
  );
  it("language change retains an open same-target selector and exact option values", async () => {
    const c = context("en");
    const props = { ...RETENTION, appId: observed.id, sourceVersion: observed.version };
    const wrap = (locale: string) => (
      <ApolloProvider client={c.client}>
        <PermissionsProvider value={{ granted: new Set(["app.update"]), loading: false }}>
          <NextIntlClientProvider locale={locale} messages={catalogs[locale]} timeZone="UTC">
            <RetentionPolicyView {...props} />
          </NextIntlClientProvider>
        </PermissionsProvider>
      </ApolloProvider>
    );
    const view = render(wrap("en"));
    await userEvent.click(
      screen.getByRole("combobox", { name: c.t("selectorLabel", { signal: "Logs" }) })
    );
    view.rerender(wrap("ja"));
    expect(screen.getByRole("listbox")).toBeInTheDocument();
    expect(screen.getAllByRole("option").map((option) => option.textContent)).toEqual(
      RETENTION_DAY_OPTIONS.map((days) =>
        createTranslator({
          locale: "ja",
          messages: catalogs.ja,
          namespace: "apps.settings.retentionFlow",
        })("days", { days })
      )
    );
    expect(RETENTION_DAY_OPTIONS).toEqual([7, 14, 30, 60, 90, 180, 365]);
    c.client.stop();
  });
  it("preserves Can's optimistic loading fence", () => {
    const c = context("en");
    render(
      <c.Wrapper allowed={false} loading>
        <RetentionPolicyView {...RETENTION} />
      </c.Wrapper>
    );
    expect(screen.getAllByRole("combobox")).toHaveLength(4);
    expect(c.requests).toHaveLength(0);
    c.client.stop();
  });
});

it.each(["null", "failed"])(
  "an initial %s app read cannot appear as a default retention card",
  async (kind) => {
    const c = context("fr", kind === "failed" ? "read-failed" : "accepted");
    if (kind === "null") c.snapshot("checkout", null);
    render(
      <c.Wrapper>
        <Card />
      </c.Wrapper>
    );
    await waitFor(() => expect(c.requests).toHaveLength(1));
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
    expect(screen.queryByText(c.t("platformDefault"))).not.toBeInTheDocument();
    expect(state.success).not.toHaveBeenCalled();
    expect(c.requests[0].operationName).toBe("GetApp");
    c.client.stop();
  }
);

it("one accepted signal refresh cannot clear another pending signal on the same app", async () => {
  const c = context("en", "deferred");
  render(
    <c.Wrapper>
      <Card />
    </c.Wrapper>
  );
  await choose("logs", c.t, 60);
  await waitFor(() => expect(c.waiting()).toBe(1));
  await choose("metrics", c.t, 90);
  await waitFor(() => expect(c.waiting()).toBe(2));
  const metricsName = c.t("selectorLabel", { signal: retentionSignalLabel("metrics", c.t) });
  expect(screen.getByRole("combobox", { name: metricsName })).toBeDisabled();
  await act(async () => {
    c.release(0);
  });
  await waitFor(() =>
    expect(state.success).toHaveBeenCalledWith(
      c.t("saveAccepted", { slug: "checkout", signal: retentionSignalLabel("logs", c.t), days: 60 })
    )
  );
  expect(screen.getByRole("combobox", { name: metricsName })).toBeDisabled();
  await act(async () => {
    c.release(1);
  });
  await waitFor(() =>
    expect(screen.getByRole("combobox", { name: metricsName })).not.toBeDisabled()
  );
  expect(
    c.requests.filter((r) => r.operationName === "SetRetentionPolicy").map((r) => r.variables)
  ).toEqual([
    { input: { appSlug: "checkout", signal: "logs", retentionDays: 60 } },
    { input: { appSlug: "checkout", signal: "metrics", retentionDays: 90 } },
  ]);
  c.client.stop();
});
