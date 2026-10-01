import { parse as parseIcu } from "@formatjs/icu-messageformat-parser";
import { readFileSync } from "node:fs";
import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
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
import type { ReactNode } from "react";
import { renderToString } from "react-dom/server";
import { hydrateRoot } from "react-dom/client";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ArchiveAppCard } from "@/app/(app)/apps/[slug]/settings/settings-client";
import { PermissionsProvider } from "@/providers/PermissionsProvider";
import { GET_APP } from "@/graphql/registry/registry.queries";
import { locales } from "@/i18n/config";
import { ArchiveAppView } from "./ArchiveApp";
import { useAppSettings } from "./use-app-settings";
import { useArchiveApp, type ArchiveSource } from "./use-archive-app";
import { ARCHIVE } from "./app-settings-members.fixtures";
const state = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  push: vi.fn(),
}));
vi.mock("sonner", () => ({ toast: state }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: state.push, replace: vi.fn() }),
  usePathname: () => "/apps/checkout/settings",
  useSearchParams: () => new URLSearchParams(),
}));
const catalogs = Object.fromEntries(
  locales.map((locale) => [locale, JSON.parse(readFileSync(`messages/${locale}.json`, "utf8"))])
);
const schema = buildSchema(readFileSync("schema.graphql", "utf8"));
const observed: ArchiveSource = {
  id: "APP_GUID_LITERAL",
  slug: "checkout",
  name: "Literal App Name",
  version: 2,
  isArchived: false,
  archivedAt: null,
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
  | "navigation-failed"
  | "deferred";
function context(locale: string, action: "archive" | "restore", initial: Mode) {
  let mode = initial;
  let snapshot: ArchiveSource | null = {
    ...observed,
    isArchived: action === "restore",
    archivedAt: action === "restore" ? "2026-09-29T23:45:00Z" : null,
  };
  const requests: { operationName: string; variables: Record<string, unknown> }[] = [];
  let reads = 0;
  let release: (() => void) | undefined;
  const fetcher = async (_url: unknown, init?: RequestInit) => {
    const request = JSON.parse(String(init?.body));
    requests.push({ operationName: request.operationName, variables: request.variables });
    const document = parse(request.query);
    expect(validate(schema, document)).toEqual([]);
    let root;
    if (request.operationName === "GetApp") {
      reads++;
      if (mode === "refresh-failed" && reads > 1) throw new Error("RAW_READ_DIAGNOSTIC");
      root = { astroliftApp: snapshot };
    } else {
      if (mode === "transport") throw new Error("RAW_TRANSPORT_DIAGNOSTIC");
      if (mode === "deferred")
        await new Promise<void>((resolve) => {
          release = resolve;
        });
      const ok = !["refused", "fallback"].includes(mode);
      const payload = {
        ...observed,
        ...snapshot,
        isArchived: request.operationName === "ArchiveApp",
        archivedAt: request.operationName === "ArchiveApp" ? "2026-09-30T12:00:00Z" : null,
      };
      if (ok && mode !== "deferred") snapshot = payload;
      root = {
        [request.operationName === "ArchiveApp" ? "archiveApp" : "restoreApp"]: {
          ok,
          errors:
            mode === "refused"
              ? [{ code: "PERMISSION_DENIED", message: "RAW_SERVER_REFUSAL", field: "appSlug" }]
              : [],
          data: ok ? payload : null,
        },
      };
    }
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
    namespace: "apps.settings.archiveFlow",
  });
  function Wrapper({ children, allowed = true }: { children: ReactNode; allowed?: boolean }) {
    return (
      <NextIntlClientProvider
        locale={locale}
        messages={catalogs[locale]}
        timeZone="America/Costa_Rica"
        now={new Date("2026-09-30T12:00:00Z")}
      >
        <ApolloProvider client={client}>
          <PermissionsProvider
            value={{ granted: new Set(allowed ? ["app.update"] : []), loading: false }}
          >
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
    reads: () => reads,
    mode: (next: Mode) => {
      mode = next;
    },
    snapshot: (next: ArchiveSource | null) => {
      snapshot = next;
    },
    release: () => release?.(),
    waiting: () => !!release,
    refresh: () => client.refetchQueries({ include: [GET_APP] }),
  };
}
function Card({ slug = "checkout" }: { slug?: string }) {
  const { app } = useAppSettings(slug);
  return app ? <ArchiveAppCard app={app} /> : null;
}
async function clickAction(action: "archive" | "restore", t: ReturnType<typeof context>["t"]) {
  await userEvent.click(await screen.findByRole("button", { name: t(action) }));
  if (action === "archive") {
    const dialog = await screen.findByRole("alertdialog");
    await userEvent.click(within(dialog).getByRole("button", { name: t("archive") }));
  }
}
beforeEach(() => {
  vi.clearAllMocks();
  state.push.mockReset();
});
describe("actual ArchiveAppCard / GetApp / HttpLink", () => {
  it.each(
    locales.flatMap((locale) =>
      (["archive", "restore"] as const).flatMap((action) =>
        (
          [
            "accepted",
            "refused",
            "fallback",
            "transport",
            "refresh-failed",
            "navigation-failed",
          ] as const
        ).map((mode) => ({ locale, action, mode }))
      )
    )
  )("$locale $action $mode", async ({ locale, action, mode }) => {
    const c = context(locale, action, mode);
    if (mode === "navigation-failed" && action === "archive")
      state.push.mockImplementationOnce(() => {
        throw new Error("RAW_NAVIGATION_FAILURE");
      });
    render(
      <c.Wrapper>
        <Card />
      </c.Wrapper>
    );
    await clickAction(action, c.t);
    const accepted = ["accepted", "refresh-failed", "navigation-failed"].includes(mode);
    await waitFor(() => expect(accepted ? state.success : state.error).toHaveBeenCalled());
    expect(c.requests.filter((r) => r.operationName !== "GetApp")).toEqual([
      {
        operationName: action === "archive" ? "ArchiveApp" : "RestoreApp",
        variables: { input: { appSlug: "checkout" } },
      },
    ]);
    if (accepted) {
      expect(state.success).toHaveBeenCalledWith(
        c.t(action === "archive" ? "archiveAccepted" : "restoreAccepted", { slug: "checkout" })
      );
      await waitFor(() => expect(c.reads()).toBe(2));
      expect(
        c.requests.filter((r) => r.operationName === "GetApp").map((r) => r.variables)
      ).toEqual([
        { slug: "checkout", includeDrift: false },
        { slug: "checkout", includeDrift: false },
      ]);
      if (mode === "refresh-failed")
        await waitFor(() => expect(state.warning).toHaveBeenCalledWith(c.t("refreshWarning")));
      if (mode === "navigation-failed" && action === "archive")
        expect(state.warning).toHaveBeenCalledWith(c.t("navigationWarning"));
      expect(state.error).not.toHaveBeenCalled();
      if (action === "archive") expect(state.push).toHaveBeenCalledExactlyOnceWith("/apps");
      else expect(state.push).not.toHaveBeenCalled();
      await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    } else {
      expect(c.reads()).toBe(1);
      expect(state.success).not.toHaveBeenCalled();
      expect(state.warning).not.toHaveBeenCalled();
      expect(state.push).not.toHaveBeenCalled();
      expect(state.error).toHaveBeenCalledWith(
        mode === "refused"
          ? "RAW_SERVER_REFUSAL"
          : mode === "transport"
            ? "RAW_TRANSPORT_DIAGNOSTIC"
            : c.t(action === "archive" ? "archiveFailed" : "restoreFailed")
      );
      if (action === "archive")
        expect(screen.getByRole("alertdialog")).toHaveTextContent(observed.name);
    }
    c.client.stop();
  });
  it.each(locales)("%s cancel and permission withdrawal do no write", async (locale) => {
    const c = context(locale, "archive", "accepted");
    const view = render(
      <c.Wrapper>
        <Card />
      </c.Wrapper>
    );
    await userEvent.click(await screen.findByRole("button", { name: c.t("archive") }));
    const shared = createTranslator({
      locale,
      messages: catalogs[locale],
      namespace: "shared.confirmation",
    });
    await userEvent.click(
      within(screen.getByRole("alertdialog")).getByRole("button", { name: shared("cancel") })
    );
    expect(c.reads()).toBe(1);
    view.rerender(
      <c.Wrapper allowed={false}>
        <Card />
      </c.Wrapper>
    );
    expect(screen.queryByRole("button", { name: c.t("archive") })).not.toBeInTheDocument();
    expect(c.requests).toHaveLength(1);
    c.client.stop();
  });
  it.each(["identity", "source", "permission", "withdrawal"])(
    "%s ABA invalidates an old hook callback",
    async (kind) => {
      const c = context("fr", "archive", "accepted");
      let old: (() => Promise<boolean>) | undefined;
      function Capture({ source }: { source: ArchiveSource | null }) {
        const h = useArchiveApp("checkout", source);
        old ??= h.onArchive;
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
          : { ...observed, ...(kind === "identity" ? { id: "OTHER_GUID" } : { version: 3 }) };
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
        expect(await old!()).toBe(false);
      });
      expect(c.requests).toHaveLength(0);
      expect(state.error).toHaveBeenCalledWith(c.t("sourceChanged"));
      c.client.stop();
    }
  );
  it("late accepted old app cannot navigate or close a newer review after source ABA", async () => {
    const c = context("en", "archive", "deferred");
    const view = render(
      <c.Wrapper>
        <Card />
      </c.Wrapper>
    );
    await clickAction("archive", c.t);
    await waitFor(() => expect(c.waiting()).toBe(true));
    c.snapshot(null);
    await act(async () => {
      await c.refresh();
    });
    c.snapshot(observed);
    await act(async () => {
      await c.refresh();
    });
    await userEvent.click(await screen.findByRole("button", { name: c.t("archive") }));
    await act(async () => {
      c.release();
    });
    await waitFor(() =>
      expect(state.success).toHaveBeenCalledWith(c.t("archiveAccepted", { slug: "checkout" }))
    );
    expect(state.push).not.toHaveBeenCalled();
    expect(screen.getByRole("alertdialog")).toHaveTextContent(observed.name);
    view.unmount();
    c.client.stop();
  });
  it("a same-name replacement closes an old review, without a write", async () => {
    const c = context("en", "archive", "accepted");
    render(
      <c.Wrapper>
        <Card />
      </c.Wrapper>
    );
    await userEvent.click(await screen.findByRole("button", { name: c.t("archive") }));
    c.snapshot({ ...observed, id: "NEW_APP_GUID" });
    await act(async () => {
      await c.refresh();
    });
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(c.requests.filter((r) => r.operationName !== "GetApp")).toHaveLength(0);
    c.client.stop();
  });
});
describe("archive locale contracts", () => {
  it.each(locales)("%s uses actual ICU/context and hydrates without recovery", async (locale) => {
    const c = context(locale, "restore", "accepted");
    const errors = vi.fn();
    const props = {
      ...ARCHIVE,
      appName: observed.name,
      isArchived: true,
      archivedAt: "2026-09-29T23:45:00Z",
    };
    const element = (
      <c.Wrapper>
        <ArchiveAppView {...props} />
      </c.Wrapper>
    );
    const html = renderToString(element);
    const host = document.createElement("div");
    host.innerHTML = html;
    document.body.append(host);
    let root: ReturnType<typeof hydrateRoot>;
    await act(async () => {
      root = hydrateRoot(host, element, { onRecoverableError: errors });
    });
    expect(host.textContent).toContain(c.t("title"));
    expect(errors).not.toHaveBeenCalled();
    await act(async () => {
      root!.unmount();
    });
    host.remove();
    c.client.stop();
    const keys = Object.keys(catalogs.en.apps.settings.archiveFlow);
    expect(Object.keys(catalogs[locale].apps.settings.archiveFlow).sort()).toEqual(keys.sort());
    for (const key of keys)
      expect(c.t(key, { at: "LITERAL_DATE", slug: "LITERAL_SLUG" })).toBeTruthy();
    expect(c.t("archivedAt", { at: "LITERAL_DATE", slug: "LITERAL_SLUG" })).toContain(
      "LITERAL_DATE"
    );
    expect(c.t("archiveAccepted", { slug: "LITERAL_SLUG" })).toContain("LITERAL_SLUG");
    expect(c.t("restoreAccepted", { slug: "LITERAL_SLUG" })).toContain("LITERAL_SLUG");
    for (const key of keys) {
      const args = (value: string) =>
        parseIcu(value)
          .flatMap((node) => (node.type === 1 ? [node.value] : []))
          .sort();
      expect(args(catalogs[locale].apps.settings.archiveFlow[key])).toEqual(
        args(catalogs.en.apps.settings.archiveFlow[key])
      );
    }
  });
});

describe("current review recovery", () => {
  it.each(locales)(
    "%s keyboard retry retains refusal and refreshes only acceptance",
    async (locale) => {
      const c = context(locale, "archive", "refused");
      render(
        <c.Wrapper>
          <Card />
        </c.Wrapper>
      );
      await userEvent.click(await screen.findByRole("button", { name: c.t("archive") }));
      const confirm = within(screen.getByRole("alertdialog")).getByRole("button", {
        name: c.t("archive"),
      });
      confirm.focus();
      await userEvent.keyboard("{Enter}");
      await waitFor(() => expect(state.error).toHaveBeenCalledWith("RAW_SERVER_REFUSAL"));
      expect(c.reads()).toBe(1);
      expect(screen.getByRole("alertdialog")).toHaveTextContent(observed.name);
      c.mode("refresh-failed");
      confirm.focus();
      await userEvent.keyboard("{Enter}");
      await waitFor(() =>
        expect(state.success).toHaveBeenCalledWith(c.t("archiveAccepted", { slug: "checkout" }))
      );
      await waitFor(() => expect(state.warning).toHaveBeenCalledWith(c.t("refreshWarning")));
      expect(state.error).toHaveBeenCalledTimes(1);
      expect(c.requests.filter((r) => r.operationName === "ArchiveApp")).toHaveLength(2);
      c.client.stop();
    }
  );
  it("same-target language change preserves review; source/authority change clears it", async () => {
    const c = context("en", "archive", "accepted");
    const props = { ...ARCHIVE, appId: observed.id, appName: observed.name };
    const wrap = (locale: string, allowed: boolean, appId: string) => (
      <ApolloProvider client={c.client}>
        <PermissionsProvider
          value={{ granted: new Set(allowed ? ["app.update"] : []), loading: false }}
        >
          <NextIntlClientProvider locale={locale} messages={catalogs[locale]} timeZone="UTC">
            <ArchiveAppView {...props} appId={appId} />
          </NextIntlClientProvider>
        </PermissionsProvider>
      </ApolloProvider>
    );
    const view = render(wrap("en", true, observed.id));
    await userEvent.click(screen.getByRole("button", { name: "Archive" }));
    view.rerender(wrap("ja", true, observed.id));
    expect(screen.getByRole("alertdialog")).toHaveTextContent(observed.name);
    view.rerender(wrap("ja", true, "REPLACED_GUID"));
    view.rerender(wrap("ja", true, observed.id));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", { name: catalogs.ja.apps.settings.archiveFlow.archive })
    );
    view.rerender(wrap("ja", false, observed.id));
    view.rerender(wrap("ja", true, observed.id));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    c.client.stop();
  });
  it("unknown or withdrawn source cannot execute a stored restore callback", async () => {
    const c = context("en", "restore", "accepted");
    let restore: (() => Promise<void>) | undefined;
    function Capture({ source }: { source: ArchiveSource | null }) {
      const h = useArchiveApp("checkout", source);
      restore ??= h.onRestore;
      return null;
    }
    const view = render(
      <c.Wrapper>
        <Capture source={null} />
      </c.Wrapper>
    );
    await act(async () => {
      await restore!();
    });
    view.rerender(
      <c.Wrapper>
        <Capture source={{ ...observed, isArchived: true }} />
      </c.Wrapper>
    );
    await act(async () => {
      await restore!();
    });
    expect(c.requests).toHaveLength(0);
    expect(state.error).toHaveBeenCalledTimes(2);
    c.client.stop();
  });
});

it("same-target cached read failure retains an open review and refusal causes no further read", async () => {
  const c = context("fr", "archive", "accepted");
  render(
    <c.Wrapper>
      <Card />
    </c.Wrapper>
  );
  await userEvent.click(await screen.findByRole("button", { name: c.t("archive") }));
  c.mode("refresh-failed");
  await act(async () => {
    await c.refresh().catch(() => {});
  });
  expect(screen.getByRole("alertdialog")).toHaveTextContent(observed.name);
  const reads = c.reads();
  c.mode("refused");
  await userEvent.click(
    within(screen.getByRole("alertdialog")).getByRole("button", { name: c.t("archive") })
  );
  await waitFor(() => expect(state.error).toHaveBeenCalledWith("RAW_SERVER_REFUSAL"));
  expect(c.reads()).toBe(reads);
  expect(screen.getByRole("alertdialog")).toHaveTextContent(observed.name);
  c.client.stop();
});

it("preserves Can's optimistic permission-loading presentation", () => {
  const c = context("en", "archive", "accepted");
  render(
    <c.Wrapper>
      <PermissionsProvider value={{ granted: new Set(), loading: true }}>
        <ArchiveAppView {...ARCHIVE} />
      </PermissionsProvider>
    </c.Wrapper>
  );
  expect(screen.getByRole("button", { name: "Archive" })).toBeInTheDocument();
  expect(c.requests).toHaveLength(0);
  c.client.stop();
});
