import { readFileSync } from "node:fs";
import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { parse as parseIcu } from "@formatjs/icu-messageformat-parser";
import { act, render, screen, waitFor } from "@testing-library/react";
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
import { ResyncSourceCard } from "@/app/(app)/apps/[slug]/settings/settings-client";
import { GET_APP } from "@/graphql/registry/registry.queries";
import { locales } from "@/i18n/config";
import { PermissionsProvider } from "@/providers/PermissionsProvider";
import { TooltipProvider } from "@/components/ui/tooltip";
import { ResyncSourceView } from "./ResyncSource";
import { useAppSettings } from "./use-app-settings";
import { useResyncSource, type ResyncSource } from "./use-resync-source";
const feedback = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn() }));
vi.mock("sonner", () => ({ toast: feedback }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => "/agents/triage/settings",
  useSearchParams: () => new URLSearchParams(),
}));
const catalogs = Object.fromEntries(
  locales.map((locale) => [locale, JSON.parse(readFileSync(`messages/${locale}.json`, "utf8"))])
);
const schema = buildSchema(readFileSync("schema.graphql", "utf8"));
const source: ResyncSource = {
  id: "LITERAL_AGENT_GUID",
  slug: "triage",
  version: 7,
  organizationSlug: "literal-org",
  projectId: "LITERAL_PROJECT_GUID",
  sourceKind: "github",
  sourceRepo: "literal-owner/repo",
  sourceUrl: "https://literal.test/repo",
  manifestPath: "agents/triage/astrolift.toml",
  deployBranch: "release/keep-exact",
  defaultBranch: "main",
  manifestHash: "LITERAL_HASH",
};
const other: ResyncSource = { ...source, id: "OTHER_AGENT_GUID", slug: "other" };
const fieldResolver: GraphQLFieldResolver<Record<string, unknown>, unknown> = (
  object,
  _args,
  _ctx,
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
type Mode =
  | "applied"
  | "in-sync"
  | "unknown"
  | "refused"
  | "fallback"
  | "transport"
  | "missing"
  | "no-payload"
  | "refresh-failed"
  | "refused-refresh-failed"
  | "deferred"
  | "read-failed";
function harness(locale: string, initial: Mode = "applied") {
  let mode = initial;
  const snapshots = new Map<string, ResyncSource | null>([
    [source.slug, source],
    [other.slug, other],
  ]);
  const requests: { operationName: string; variables: Record<string, unknown> }[] = [];
  const counts = new Map<string, number>();
  const waiting: (() => void)[] = [];
  const client = new ApolloClient({
    cache: new InMemoryCache(),
    link: new HttpLink({
      uri: "https://test.invalid/graphql",
      fetch: (async (_url, init) => {
        const request = JSON.parse(String(init?.body));
        requests.push(request);
        const document = parse(request.query);
        expect(validate(schema, document)).toEqual([]);
        const acceptedMode = mode;
        let root;
        if (request.operationName === "GetApp") {
          const slug = request.variables.slug;
          const count = (counts.get(slug) ?? 0) + 1;
          counts.set(slug, count);
          if (
            mode === "read-failed" ||
            (count > 1 && ["refresh-failed", "refused-refresh-failed"].includes(mode))
          )
            throw new Error("RAW_SOURCE_READ_ERROR");
          root = { astroliftApp: snapshots.get(slug) ?? null };
        } else if (request.operationName === "ResyncAstroliftManifestFromRepo") {
          if (mode === "transport") throw new Error("RAW_PROVIDER_TRANSPORT_ERROR");
          if (mode === "missing")
            return new Response(JSON.stringify({ data: null }), {
              headers: { "Content-Type": "application/json" },
            });
          if (mode === "deferred") await new Promise<void>((release) => waiting.push(release));
          const ok = !["refused", "fallback", "refused-refresh-failed"].includes(acceptedMode);
          root = {
            resyncAstroliftManifestFromRepo: {
              ok,
              errors: ["refused", "refused-refresh-failed"].includes(acceptedMode)
                ? [{ code: "CONFLICT", message: "RAW_STAGED_DRAFT_REFUSAL" }]
                : [],
              data:
                ok && acceptedMode !== "no-payload"
                  ? {
                      syncState:
                        acceptedMode === "in-sync"
                          ? "in_sync"
                          : acceptedMode === "unknown"
                            ? "FUTURE_PROVIDER_STATE"
                            : "applied",
                      summary:
                        "RAW_PROVIDER_SUMMARY literal-owner/repo release/keep-exact agents/triage/astrolift.toml",
                      workloadsAdded: [],
                      workloadsRemoved: [],
                      workloadsChanged: ["LITERAL_WORKLOAD_SLUG"],
                      managedServicesAdded: [],
                      managedServicesRemoved: [],
                      envKeysChanged: 0,
                      schedulesChanged: 0,
                    }
                  : null,
            },
          };
        } else throw new Error("Unexpected request " + request.operationName);
        const result = await execute({
          schema,
          document,
          rootValue: root,
          variableValues: request.variables,
          fieldResolver,
        });
        expect(result.errors).toBeUndefined();
        return new Response(JSON.stringify(result), {
          headers: { "Content-Type": "application/json" },
        });
      }) as typeof fetch,
    }),
  });
  const t = createTranslator({
    locale,
    messages: catalogs[locale],
    namespace: "apps.settings.agentResyncFlow",
  });
  function Wrapper({
    children,
    allowed = true,
    permissionsLoading = false,
    selectedLocale = locale,
  }: {
    children: ReactNode;
    allowed?: boolean;
    permissionsLoading?: boolean;
    selectedLocale?: string;
  }) {
    return (
      <NextIntlClientProvider
        locale={selectedLocale}
        messages={catalogs[selectedLocale]}
        timeZone="America/Costa_Rica"
        now={new Date("2026-10-01T12:00:00Z")}
      >
        <ApolloProvider client={client}>
          <PermissionsProvider
            value={{ granted: new Set(allowed ? ["app.update"] : []), loading: permissionsLoading }}
          >
            <TooltipProvider>{children}</TooltipProvider>
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
    snapshot: (slug: string, value: ResyncSource | null) => snapshots.set(slug, value),
    reads: (slug = source.slug) => counts.get(slug) ?? 0,
    release: (index = 0) => waiting[index]?.(),
    waiting: () => waiting.length,
    refresh: () => client.refetchQueries({ include: [GET_APP] }),
  };
}
function Card({ slug = source.slug, agentMode = true }: { slug?: string; agentMode?: boolean }) {
  const { app } = useAppSettings(slug);
  return app ? <ResyncSourceCard app={app} agentMode={agentMode} /> : null;
}
type Result = ReturnType<typeof useResyncSource>;
function Probe({
  observed = source,
  slug = source.slug,
  capture,
}: {
  observed?: ResyncSource | null;
  slug?: string;
  capture: (result: Result) => void;
}) {
  const result = useResyncSource(slug, observed, true);
  useLayoutEffect(() => {
    capture(result);
  });
  return <ResyncSourceView {...result} lastResyncAt={null} agentMode />;
}
beforeEach(() => vi.clearAllMocks());
describe("actual agent ResyncSourceCard, schema-validated HttpLink", () => {
  it.each(
    locales.flatMap((locale) =>
      (
        [
          "applied",
          "in-sync",
          "unknown",
          "refused",
          "fallback",
          "transport",
          "missing",
          "no-payload",
          "refresh-failed",
          "refused-refresh-failed",
        ] as const
      ).map((mode) => [locale, mode] as const)
    )
  )("%s / %s keeps literal request and truthful outcome", async (locale, mode) => {
    const h = harness(locale, mode);
    render(
      <h.Wrapper>
        <Card />
      </h.Wrapper>
    );
    const button = await screen.findByRole("button", { name: h.t("button") });
    expect(screen.getByText(h.t("title"))).toBeInTheDocument();
    await userEvent.click(button);
    await waitFor(() => expect(button).not.toBeDisabled());
    const mutations = h.requests.filter(
      (r) => r.operationName === "ResyncAstroliftManifestFromRepo"
    );
    expect(mutations).toHaveLength(1);
    expect(mutations[0].variables).toEqual({ input: { appSlug: source.slug } });
    for (const read of h.requests.filter((r) => r.operationName === "GetApp"))
      expect(read.variables).toEqual({ slug: source.slug, includeDrift: false });
    if (["refused", "fallback", "transport", "missing", "refused-refresh-failed"].includes(mode)) {
      expect(h.reads()).toBe(1);
      expect(feedback.success).not.toHaveBeenCalled();
      expect(feedback.warning).not.toHaveBeenCalled();
      expect(feedback.error).toHaveBeenCalledWith(
        ["refused", "refused-refresh-failed"].includes(mode)
          ? "RAW_STAGED_DRAFT_REFUSAL"
          : mode === "transport"
            ? "RAW_PROVIDER_TRANSPORT_ERROR"
            : h.t(mode === "missing" ? "noResponse" : "failed")
      );
    } else {
      expect(h.reads()).toBe(2);
      expect(feedback.error).not.toHaveBeenCalled();
      if (mode === "no-payload") {
        expect(feedback.warning).toHaveBeenCalledWith(
          h.t("acceptedWithoutDetails", { slug: source.slug })
        );
        expect(feedback.success).not.toHaveBeenCalled();
      } else {
        expect(feedback.success).toHaveBeenCalledWith(
          h.t(
            mode === "in-sync"
              ? "agentInSync"
              : mode === "unknown"
                ? "reportedState"
                : "agentApplied",
            { slug: source.slug, state: "FUTURE_PROVIDER_STATE" }
          ),
          {
            description: expect.stringContaining(
              "RAW_PROVIDER_SUMMARY literal-owner/repo release/keep-exact agents/triage/astrolift.toml"
            ),
          }
        );
        if (mode === "refresh-failed")
          expect(feedback.warning).toHaveBeenCalledWith(
            h.t("refreshWarning", { slug: source.slug })
          );
        else expect(feedback.warning).not.toHaveBeenCalled();
      }
    }
    h.client.stop();
  });
  it.each(locales)("%s refusal then keyboard retry preserves exact target", async (locale) => {
    const h = harness(locale, "refused");
    render(
      <h.Wrapper>
        <Card />
      </h.Wrapper>
    );
    const button = await screen.findByRole("button", { name: h.t("button") });
    button.focus();
    await userEvent.keyboard("{Enter}");
    await waitFor(() => expect(feedback.error).toHaveBeenCalledWith("RAW_STAGED_DRAFT_REFUSAL"));
    h.mode("applied");
    await userEvent.keyboard("{Enter}");
    await waitFor(() => expect(feedback.success).toHaveBeenCalled());
    expect(
      h.requests
        .filter((r) => r.operationName === "ResyncAstroliftManifestFromRepo")
        .map((r) => r.variables)
    ).toEqual([{ input: { appSlug: source.slug } }, { input: { appSlug: source.slug } }]);
    expect(h.reads()).toBe(2);
    h.client.stop();
  });
  it.each([
    "id",
    "sourceRepo",
    "sourceUrl",
    "deployBranch",
    "defaultBranch",
    "manifestPath",
    "sourceKind",
    "manifestHash",
    "projectId",
    "organizationSlug",
    "version",
    "withdrawal",
    "permission",
  ])("invalidates old callbacks across %s and ABA", async (field) => {
    const h = harness("fr");
    let result!: Result;
    const capture = (value: Result) => {
      result = value;
    };
    const frame = (observed: ResyncSource | null, allowed = true) => (
      <h.Wrapper allowed={allowed}>
        <Probe observed={observed} capture={capture} />
      </h.Wrapper>
    );
    const view = render(frame(source));
    const old = result.onResync;
    const replacement =
      field === "withdrawal"
        ? null
        : field === "permission"
          ? source
          : { ...source, [field]: field === "version" ? 8 : "DIFFERENT_LITERAL" };
    view.rerender(frame(replacement, field !== "permission"));
    await act(() => old());
    view.rerender(frame(source));
    await act(() => old());
    expect(h.requests).toHaveLength(0);
    expect(feedback.error).toHaveBeenCalledWith(h.t("sourceChanged"));
    await act(() => result.onResync());
    expect(
      h.requests.filter((r) => r.operationName === "ResyncAstroliftManifestFromRepo")
    ).toHaveLength(1);
    h.client.stop();
  });
  it("old accepted result cannot refresh a replacement target or clear its pending request", async () => {
    const h = harness("de", "deferred");
    let result!: Result;
    const capture = (value: Result) => {
      result = value;
    };
    const frame = (observed: ResyncSource) => (
      <h.Wrapper>
        <Probe observed={observed} slug={observed.slug} capture={capture} />
      </h.Wrapper>
    );
    const view = render(frame(source));
    let first!: Promise<void>;
    act(() => {
      first = result.onResync();
    });
    await waitFor(() => expect(h.waiting()).toBe(1));
    view.rerender(frame(other));
    let second!: Promise<void>;
    act(() => {
      second = result.onResync();
    });
    await waitFor(() => expect(h.waiting()).toBe(2));
    await act(async () => {
      h.release();
      await first;
    });
    expect(result.loading).toBe(true);
    expect(h.reads(source.slug)).toBe(0);
    expect(h.reads(other.slug)).toBe(0);
    await act(async () => {
      h.release(1);
      await second;
    });
    expect(result.loading).toBe(false);
    expect(h.reads(other.slug)).toBe(1);
    expect(feedback.success.mock.calls.map((args) => args[0])).toEqual([
      h.t("agentApplied", { slug: source.slug }),
      h.t("agentApplied", { slug: other.slug }),
    ]);
    h.client.stop();
  });
  it.each(locales)(
    "%s actual card withdrawal blocks an old DOM action and retains raw refusal on retry",
    async (locale) => {
      const h = harness(locale, "refused");
      const frame = (allowed: boolean) => (
        <h.Wrapper allowed={allowed}>
          <Card />
        </h.Wrapper>
      );
      const view = render(frame(true));
      const oldButton = await screen.findByRole("button", { name: h.t("button") });
      view.rerender(frame(false));
      expect(screen.queryByRole("button")).not.toBeInTheDocument();
      await userEvent.click(oldButton);
      expect(
        h.requests.filter((request) => request.operationName === "ResyncAstroliftManifestFromRepo")
      ).toHaveLength(0);
      view.rerender(frame(true));
      await userEvent.click(await screen.findByRole("button", { name: h.t("button") }));
      await waitFor(() => expect(feedback.error).toHaveBeenCalledWith("RAW_STAGED_DRAFT_REFUSAL"));
      h.snapshot(source.slug, null);
      await act(async () => {
        await h.refresh();
      });
      expect(screen.queryByRole("button")).not.toBeInTheDocument();
      await userEvent.click(oldButton);
      expect(
        h.requests.filter((request) => request.operationName === "ResyncAstroliftManifestFromRepo")
      ).toHaveLength(1);
      h.client.stop();
    }
  );

  it("does not dispatch twice while the same observed source is pending", async () => {
    const h = harness("en", "deferred");
    let result!: Result;
    render(
      <h.Wrapper>
        <Probe
          capture={(value) => {
            result = value;
          }}
        />
      </h.Wrapper>
    );
    let accepted!: Promise<void>;
    act(() => {
      accepted = result.onResync();
    });
    await waitFor(() => expect(h.waiting()).toBe(1));
    await act(() => result.onResync());
    expect(h.waiting()).toBe(1);
    await act(async () => {
      h.release();
      await accepted;
    });
    expect(feedback.success).toHaveBeenCalledTimes(1);
    h.client.stop();
  });
  it("locale change preserves the current observed callback and translates the new action", async () => {
    const h = harness("en");
    let result!: Result;
    const frame = (locale: string) => (
      <h.Wrapper selectedLocale={locale}>
        <Probe
          capture={(value) => {
            result = value;
          }}
        />
      </h.Wrapper>
    );
    const view = render(frame("en"));
    const old = result.onResync;
    view.rerender(frame("ja"));
    expect(
      screen.getByRole("button", { name: catalogs.ja.apps.settings.agentResyncFlow.button })
    ).toBeEnabled();
    await act(() => old());
    expect(feedback.error).not.toHaveBeenCalled();
    h.client.stop();
  });
  it("same-target cached read failure retains source observation", async () => {
    const h = harness("en");
    render(
      <h.Wrapper>
        <Card />
      </h.Wrapper>
    );
    const button = await screen.findByRole("button", { name: h.t("button") });
    h.mode("read-failed");
    await act(async () => {
      await h.refresh().catch(() => undefined);
    });
    expect(button).toBeEnabled();
    h.mode("applied");
    await userEvent.click(button);
    await waitFor(() => expect(feedback.success).toHaveBeenCalled());
    h.client.stop();
  });
  it.each(["read-failed", "not-found"])(
    "first %s read cannot expose a healthy source action",
    async (mode) => {
      const h = harness("en", mode === "read-failed" ? mode : "applied");
      if (mode === "not-found") h.snapshot(source.slug, null);
      render(
        <h.Wrapper>
          <Card />
        </h.Wrapper>
      );
      await waitFor(() => expect(h.reads()).toBe(1));
      await act(async () => {});
      expect(screen.queryByRole("button")).not.toBeInTheDocument();
      expect(h.requests.filter((r) => r.operationName !== "GetApp")).toHaveLength(0);
      h.client.stop();
    }
  );
  it("preserves existing Can permission-loading optimism and confirmed denial", async () => {
    const h = harness("en");
    const frame = (allowed: boolean, loading: boolean) => (
      <h.Wrapper allowed={allowed} permissionsLoading={loading}>
        <ResyncSourceView loading={false} onResync={async () => {}} lastResyncAt={null} agentMode />
      </h.Wrapper>
    );
    const view = render(frame(false, true));
    expect(screen.getByRole("button", { name: h.t("button") })).toBeEnabled();
    view.rerender(frame(false, false));
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    h.client.stop();
  });
  it("keeps ordinary manifest server summary literal", async () => {
    const h = harness("es");
    render(
      <h.Wrapper>
        <Card agentMode={false} />
      </h.Wrapper>
    );
    await userEvent.click(
      await screen.findByRole("button", { name: catalogs.es.apps.settings.resync.button })
    );
    await waitFor(() =>
      expect(feedback.success).toHaveBeenCalledWith(expect.stringContaining("RAW_PROVIDER_SUMMARY"))
    );
    h.client.stop();
  });
});
describe("all-eight agent resync messages and request-clock hydration", () => {
  it.each(locales)(
    "%s preserves ICU identities and hydrates localized source/date copy",
    async (locale) => {
      const h = harness(locale);
      const errors = vi.fn();
      for (const [key, english] of Object.entries(catalogs.en.apps.settings.agentResyncFlow)) {
        const translated = catalogs[locale].apps.settings.agentResyncFlow[key];
        const args = (message: string) =>
          parseIcu(message)
            .filter((node) => node.type === 1)
            .map((node) => node.value)
            .sort();
        expect(args(translated)).toEqual(args(english as string));
        expect(h.t(key, { slug: "LITERAL_GUID_SLUG", state: "FUTURE_RAW_STATE" })).toBeTruthy();
        if (locale !== "en") expect(translated).not.toBe(english);
      }
      const ui = (
        <h.Wrapper>
          <ResyncSourceView
            loading={false}
            onResync={async () => {}}
            agentMode
            lastResyncAt="2026-10-01T11:55:00Z"
          />
        </h.Wrapper>
      );
      const html = renderToString(ui);
      expect(html).toContain(h.t("title"));
      const container = document.createElement("div");
      container.innerHTML = html;
      document.body.append(container);
      let root!: ReturnType<typeof hydrateRoot>;
      await act(async () => {
        root = hydrateRoot(container, ui, { onRecoverableError: errors });
      });
      expect(container.innerHTML).toContain(h.t("title"));
      expect(container.textContent).toContain(
        new Intl.RelativeTimeFormat(locale, { numeric: "auto" }).format(-5, "minute")
      );
      expect(container.textContent).not.toContain("Invalid Date");
      expect(errors).not.toHaveBeenCalled();
      await act(async () => root.unmount());
      container.remove();
      h.client.stop();
    }
  );
});
