import { readFileSync } from "node:fs";
import path from "node:path";
import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { parse, type MessageFormatElement } from "@formatjs/icu-messageformat-parser";
import { act, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createTranslator, NextIntlClientProvider } from "next-intl";
import { hydrateRoot } from "react-dom/client";
import { renderToString } from "react-dom/server";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { locales } from "@/i18n/config";
import { TOKENS, TOKEN_DETAIL, SCOPE_CATALOG } from "../teams/teams-tokens.fixtures";
import { ScopePicker } from "./ScopePicker";
import { TokenDetailScreen } from "./TokenDetailScreen";
import { TokensScreen } from "./TokensScreen";
import { useTokens } from "./use-tokens";
import { useTokenDetail } from "./use-token-detail";
import { localizedTokensList, TOKENS_LIST, tokensVariables, narrowTokens } from "./tokens-list";
import { presetLabel, scopePresentation, STOCK_SCOPE_COPY } from "./scope-presentation";

const notifications = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  allow: true,
}));
vi.mock("sonner", () => ({ toast: notifications }));
vi.mock("@/lib/permissions/use-my-permissions", () => ({
  useMyPermissions: () => ({ can: () => notifications.allow, loading: false }),
}));
vi.mock("next/navigation", () => ({
  usePathname: () => "/tokens",
  useSearchParams: () => new URLSearchParams(),
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));
const catalogs = Object.fromEntries(
  locales.map((locale) => [
    locale,
    JSON.parse(readFileSync(path.resolve("messages", `${locale}.json`), "utf8")),
  ])
);
const tFor = (locale: string) =>
  createTranslator({ locale, messages: catalogs[locale], namespace: "apiKeys" });
const now = new Date("2026-09-30T12:00:00Z");
const token = {
  __typename: "AstroliftApiToken",
  ...TOKENS[0],
  name: "RAW_USER_NAME",
  createdAt: "2026-09-30T00:30:00Z",
};
const plaintext = "alft_at_SYNTHETIC_TEST_ONLY";
const stockScopes = Object.entries(STOCK_SCOPE_COPY).map(([value, copy]) => ({
  ...SCOPE_CATALOG.scopes[0],
  value,
  label: copy.label,
  description: copy.description,
  available: value !== "manage:clusters",
  unavailableReason:
    value === "manage:clusters" ? "Your roles grant none of what this scope unlocks." : "",
  surface: value === "admin" ? "administration" : "clusters",
  permissions: ["RAW_PERMISSION_ID"],
  sensitive: value === "admin",
}));
const catalog = {
  scopes: stockScopes,
  presets: [
    {
      key: "cluster_operator",
      label: "Cluster operator",
      scopes: ["read:clusters", "write:clusters", "manage:clusters"],
    },
  ],
};
beforeEach(() => {
  notifications.allow = true;
  notifications.success.mockClear();
  notifications.error.mockClear();
  notifications.warning.mockClear();
});
type Mode =
  | "ok"
  | "refused"
  | "no-diagnostic"
  | "refresh-failed"
  | "transport-failed"
  | "read-failed"
  | "refused-refresh-failed";
type Request = { operationName: string; variables: Record<string, unknown> };
function contextFor(locale: string, mode: Mode = "ok") {
  const requests: Request[] = [];
  const counts = new Map<string, number>();
  let currentMode = mode;
  const fetcher = vi.fn(async (_url: unknown, init?: RequestInit) => {
    const request = JSON.parse(String(init?.body)) as Request;
    requests.push(request);
    counts.set(request.operationName, (counts.get(request.operationName) ?? 0) + 1);
    let data;
    if (request.operationName === "Me")
      data = {
        me: {
          id: "actor-id",
          profile: { id: "profile-id", username: token.user.username },
          modules: [],
        },
      };
    else if (request.operationName === "GetApiTokenScopeCatalog")
      data = { astroliftApiTokenScopeCatalog: catalog };
    else if (
      request.operationName === "ListApiTokens" ||
      request.operationName === "ListApiTokensPage"
    ) {
      if (
        currentMode === "read-failed" ||
        (["refresh-failed", "refused-refresh-failed"].includes(currentMode) &&
          counts.get(request.operationName)! > 1)
      )
        throw new Error("RAW_READ_FAILURE");
      data =
        request.operationName === "ListApiTokens"
          ? { astroliftApiTokens: [token] }
          : { astroliftApiTokensPage: { items: [token], totalCount: 1, nextCursor: null } };
    } else {
      if (currentMode === "transport-failed") throw new Error("RAW_TRANSPORT_FAILURE");
      const field =
        request.operationName === "CreateApiToken"
          ? "createApiToken"
          : request.operationName === "RevokeApiToken"
            ? "revokeApiToken"
            : null;
      if (!field) throw new Error(request.operationName);
      const refused =
        currentMode === "refused" ||
        currentMode === "no-diagnostic" ||
        currentMode === "refused-refresh-failed";
      const input = request.variables.input as { name?: string; scopes?: string[] };
      data = {
        [field]: {
          ok: !refused,
          errors: ["refused", "refused-refresh-failed"].includes(currentMode)
            ? [{ code: "DENIED", message: "RAW_POLICY_REFUSAL", field: null }]
            : [],
          data: refused
            ? null
            : field === "createApiToken"
              ? { apiToken: { ...token, name: input.name, scopes: input.scopes }, plaintext }
              : { id: token.id, deleted: true },
        },
      };
    }
    return new Response(JSON.stringify({ data }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  });
  const client = new ApolloClient({
    cache: new InMemoryCache(),
    link: new HttpLink({ uri: "https://example.invalid/graphql", fetch: fetcher as typeof fetch }),
  });
  const errors = vi.fn();
  const wrapper = ({ children }: { children: ReactNode }) => (
    <NextIntlClientProvider
      locale={locale}
      messages={catalogs[locale]}
      now={now}
      timeZone="America/Los_Angeles"
      onError={errors}
    >
      <ApolloProvider client={client}>{children}</ApolloProvider>
    </NextIntlClientProvider>
  );
  return {
    requests,
    client,
    wrapper,
    errors,
    setMode: (mode: Mode) => {
      currentMode = mode;
    },
  };
}
function Connected() {
  return (
    <TokensScreen
      {...useTokens()}
      renderScopePicker={(props) => (
        <ScopePicker catalog={catalog} loading={false} error={undefined} {...props} />
      )}
    />
  );
}
function Detail() {
  return <TokenDetailScreen {...useTokenDetail(token.id)} />;
}
function flatten(value: Record<string, unknown>, prefix = ""): Record<string, string> {
  return Object.fromEntries(
    Object.entries(value).flatMap(([key, item]) => {
      const name = prefix ? `${prefix}.${key}` : key;
      return typeof item === "string"
        ? [[name, item]]
        : Object.entries(flatten(item as Record<string, unknown>, name));
    })
  );
}
function argumentsOf(nodes: MessageFormatElement[]): string[] {
  return [
    ...new Set(
      nodes.flatMap((node): string[] => {
        if (node.type === 0 || node.type === 7) return [];
        if (node.type === 8) return [`tag:${node.value}`, ...argumentsOf(node.children)];
        if (node.type === 5 || node.type === 6)
          return [
            `${node.type}:${node.value}`,
            ...Object.values(node.options).flatMap((option) => argumentsOf(option.value)),
          ];
        return [`${node.type}:${node.value}`];
      })
    ),
  ].sort();
}

describe("API keys genuine presentation and real adapter outcomes", () => {
  it.each(locales)("%s keeps ICU arguments and exact query/view identities", (locale) => {
    const english = flatten(catalogs.en.apiKeys),
      translated = flatten(catalogs[locale].apiKeys);
    expect(Object.keys(translated).sort()).toEqual(Object.keys(english).sort());
    for (const [key, value] of Object.entries(english))
      expect(argumentsOf(parse(translated[key]))).toEqual(argumentsOf(parse(value)));
    if (locale !== "en") {
      for (const key of Object.keys(english).filter(
        (k) => k.endsWith(".description") || k === "createDescription"
      ))
        expect(translated[key]).not.toBe(english[key]);
    }
    const localized = localizedTokensList(tFor(locale));
    expect(localized.views.map(({ key, filters }) => ({ key, filters }))).toEqual(
      TOKENS_LIST.views.map(({ key, filters }) => ({ key, filters }))
    );
    expect(
      localized.fields.map(({ key, options }) => ({ key, values: options?.map((o) => o.value) }))
    ).toEqual(
      TOKENS_LIST.fields.map(({ key, options }) => ({ key, values: options?.map((o) => o.value) }))
    );
    expect(
      tokensVariables(
        { status: "active" },
        { q: " EXACT_SEARCH ", pageSize: 25, after: "RAW_CURSOR" }
      )
    ).toEqual({ search: "EXACT_SEARCH", limit: 100, after: "RAW_CURSOR" });
    expect(narrowTokens([token], { scope: "read:apps", owner: "me" }, token.user.username)).toEqual(
      [token]
    );
    expect(tFor(locale)("permissionCount", { count: 1 })).not.toMatch(/\{count/);
    expect(tFor(locale)("picker.unlocks", { count: 2 })).not.toMatch(/\{count/);
  });
  it.each(locales)(
    "%s translates only exact reviewed scope metadata and preserves future/custom text",
    (locale) => {
      const t = tFor(locale),
        scope = stockScopes[0];
      expect(scopePresentation(scope, t)).toMatchObject({
        label: t("catalog.scope0.label"),
        description: t("catalog.scope0.description"),
      });
      expect(
        scopePresentation(
          {
            ...scope,
            label: "CUSTOM_LABEL",
            description: "NEW_SERVER_DESCRIPTION",
            unavailableReason: "RAW_POLICY_REASON",
          },
          t
        )
      ).toEqual({
        label: "CUSTOM_LABEL",
        description: "NEW_SERVER_DESCRIPTION",
        unavailableReason: "RAW_POLICY_REASON",
      });
      for (const value of ["future:scope", "constructor", "toString"])
        expect(scopePresentation({ ...scope, value }, t)).toMatchObject({
          label: scope.label,
          description: scope.description,
        });
      expect(presetLabel("cluster_operator", "Cluster operator", t)).toBe(
        t("picker.presets.cluster_operator")
      );
      expect(presetLabel("cluster_operator", "CUSTOM_PRESET", t)).toBe("CUSTOM_PRESET");
    }
  );
  it.each(locales)(
    "%s respects preset admission, removable denied scopes and raw permission IDs",
    (locale) => {
      const t = tFor(locale),
        errors = vi.fn();
      let values: string[] = [];
      const view = render(
        <NextIntlClientProvider
          locale={locale}
          messages={catalogs[locale]}
          now={now}
          timeZone="UTC"
          onError={errors}
        >
          <ScopePicker
            catalog={catalog}
            loading={false}
            error={undefined}
            value={[]}
            onChange={(v) => {
              values = v;
            }}
          />
        </NextIntlClientProvider>
      );
      const label = t("catalog.scope5.label");
      expect(screen.getByRole("checkbox", { name: new RegExp(label) })).toBeDisabled();
      fireEvent.click(screen.getByRole("button", { name: t("picker.presets.cluster_operator") }));
      expect(values).toEqual(["read:clusters", "write:clusters"]);
      view.rerender(
        <NextIntlClientProvider
          locale={locale}
          messages={catalogs[locale]}
          now={now}
          timeZone="UTC"
          onError={errors}
        >
          <ScopePicker
            catalog={catalog}
            loading={false}
            error={undefined}
            value={["manage:clusters", "admin"]}
            onChange={(v) => {
              values = v;
            }}
          />
        </NextIntlClientProvider>
      );
      expect(screen.getByRole("alert")).toHaveTextContent(t("picker.adminWarning"));
      fireEvent.click(screen.getByRole("checkbox", { name: new RegExp(label) }));
      expect(values).toEqual(["admin"]);
      fireEvent.click(
        screen.getAllByRole("button", { name: t("picker.unlocks", { count: 1 }) })[0]
      );
      expect(screen.getByText("RAW_PERMISSION_ID")).toBeInTheDocument();
      expect(errors).not.toHaveBeenCalled();
    }
  );
  describe.each(["ok", "refused", "no-diagnostic", "refresh-failed", "transport-failed"] as const)(
    "%s",
    (mode) => {
      it.each(locales)(
        "%s actual create keeps payload and refused drafts; committed refresh failures retain reveal",
        async (locale) => {
          const context = contextFor(locale, mode),
            t = tFor(locale),
            view = render(<Connected />, { wrapper: context.wrapper });
          try {
            await screen.findByText(token.name);
            fireEvent.click(screen.getByRole("button", { name: t("newToken") }));
            fireEvent.change(screen.getByLabelText(t("name")), {
              target: { value: " EXACT_TOKEN_NAME " },
            });
            fireEvent.change(screen.getByLabelText(t("expiresDays")), { target: { value: "17" } });
            fireEvent.click(screen.getByRole("button", { name: t("create") }));
            const accepted = mode === "ok" || mode === "refresh-failed";
            await waitFor(() =>
              expect(
                context.requests.filter((r) => r.operationName === "CreateApiToken")
              ).toHaveLength(1)
            );
            expect(
              context.requests.find((r) => r.operationName === "CreateApiToken")!.variables
            ).toEqual({
              input: {
                name: "EXACT_TOKEN_NAME",
                expiresInDays: 17,
                scopes: ["read:apps", "read:clusters", "mcp:read"],
              },
            });
            if (accepted) {
              await screen.findByText(plaintext);
              expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
              expect(notifications.error).not.toHaveBeenCalled();
              if (mode === "refresh-failed")
                expect(notifications.warning).toHaveBeenCalledWith(t("refreshWarning"));
            } else {
              await waitFor(() =>
                expect(notifications.error).toHaveBeenCalledWith(
                  mode === "refused"
                    ? "RAW_POLICY_REFUSAL"
                    : mode === "transport-failed"
                      ? "RAW_TRANSPORT_FAILURE"
                      : t("createFailed")
                )
              );
              expect(screen.getByLabelText(t("name"))).toHaveValue(" EXACT_TOKEN_NAME ");
              expect(screen.getByLabelText(t("expiresDays"))).toHaveValue(17);
              expect(screen.getByRole("dialog")).toBeInTheDocument();
              expect(screen.queryByText(plaintext)).not.toBeInTheDocument();
              expect(notifications.success).not.toHaveBeenCalled();
            }
            expect(context.errors).not.toHaveBeenCalled();
          } finally {
            view.unmount();
            context.client.stop();
          }
        }
      );
      it.each(locales)(
        "%s actual revoke respects refusal and refresh outcome without changing target",
        async (locale) => {
          const context = contextFor(locale, mode),
            t = tFor(locale),
            hook = renderHook(() => useTokens(), { wrapper: context.wrapper });
          try {
            await waitFor(() => expect(hook.result.current.rows).toHaveLength(1));
            const accepted = mode === "ok" || mode === "refresh-failed";
            await act(async () => {
              if (accepted) await hook.result.current.onRevoke(token);
              else
                await expect(hook.result.current.onRevoke(token)).rejects.toThrow(
                  mode === "refused"
                    ? "RAW_POLICY_REFUSAL"
                    : mode === "transport-failed"
                      ? "RAW_TRANSPORT_FAILURE"
                      : t("revokeFailed")
                );
            });
            expect(
              context.requests.find((r) => r.operationName === "RevokeApiToken")!.variables
            ).toEqual({ input: { id: token.id } });
            if (accepted) expect(notifications.success).toHaveBeenCalledWith(t("revokedFeedback"));
            else expect(notifications.success).not.toHaveBeenCalled();
            if (mode === "refresh-failed")
              expect(notifications.warning).toHaveBeenCalledWith(t("refreshWarning"));
            expect(context.errors).not.toHaveBeenCalled();
          } finally {
            hook.unmount();
            context.client.stop();
          }
        }
      );
    }
  );
  it.each(locales)(
    "%s copy success waits for actual clipboard and refusal keeps one-time value",
    async (locale) => {
      const context = contextFor(locale),
        t = tFor(locale),
        hook = renderHook(() => useTokens(), { wrapper: context.wrapper });
      try {
        await act(async () => {
          expect(
            await hook.result.current.onCreate({
              name: "RAW_NAME",
              expiresInDays: "0",
              scopes: ["read:apps"],
            })
          ).toBe(true);
        });
        expect(
          context.requests.find((r) => r.operationName === "CreateApiToken")!.variables
        ).toEqual({ input: { name: "RAW_NAME", expiresInDays: null, scopes: ["read:apps"] } });
        let complete!: () => void;
        const writeText = vi.fn(
          () =>
            new Promise<void>((resolve) => {
              complete = resolve;
            })
        );
        const descriptor = Object.getOwnPropertyDescriptor(navigator, "clipboard");
        Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
        try {
          let pending!: Promise<void>;
          act(() => {
            pending = hook.result.current.onCopyPlaintext();
          });
          expect(notifications.success).not.toHaveBeenCalled();
          expect(writeText).toHaveBeenCalledWith(plaintext);
          await act(async () => {
            complete();
            await pending;
          });
          expect(notifications.success).toHaveBeenCalledWith(t("copied"));
          notifications.success.mockClear();
          writeText.mockImplementation(() => Promise.reject(new Error("CLIPBOARD_DENIED")));
          await act(async () => hook.result.current.onCopyPlaintext());
          expect(notifications.error).toHaveBeenCalledWith(t("copyFailed"));
          expect(notifications.success).not.toHaveBeenCalled();
          expect(hook.result.current.createdToken?.plaintext).toBe(plaintext);
          Object.defineProperty(navigator, "clipboard", { configurable: true, value: undefined });
          await act(async () => hook.result.current.onCopyMcpEndpoint());
          expect(notifications.success).not.toHaveBeenCalled();
          Object.defineProperty(navigator, "clipboard", {
            configurable: true,
            value: { writeText: vi.fn(async () => {}) },
          });
          await act(async () => hook.result.current.onCopyMcpEndpoint());
          expect(notifications.success).toHaveBeenCalledWith(t("endpointCopied"));
        } finally {
          if (descriptor) Object.defineProperty(navigator, "clipboard", descriptor);
          else Reflect.deleteProperty(navigator, "clipboard");
        }
      } finally {
        hook.unmount();
        context.client.stop();
      }
    }
  );
  it.each(locales)(
    "%s never treats a metadata transport failure as not-found and retries real read",
    async (locale) => {
      const context = contextFor(locale, "read-failed"),
        t = tFor(locale),
        view = render(<Detail />, { wrapper: context.wrapper });
      try {
        await screen.findByText("RAW_READ_FAILURE");
        expect(screen.queryByText(t("detail.notFoundTitle"))).not.toBeInTheDocument();
        context.setMode("ok");
        fireEvent.click(screen.getByRole("button", { name: t("detail.retry") }));
        await screen.findAllByText(token.name);
        expect(screen.queryByRole("alert")).not.toBeInTheDocument();
        expect(context.requests.filter((r) => r.operationName === "ListApiTokens")).toHaveLength(2);
        expect(context.errors).not.toHaveBeenCalled();
      } finally {
        view.unmount();
        context.client.stop();
      }
    }
  );
  it.each(locales)("%s retained token remains visible on read refresh failure", async (locale) => {
    const context = contextFor(locale),
      hook = renderHook(() => useTokenDetail(token.id), { wrapper: context.wrapper });
    try {
      await waitFor(() => expect(hook.result.current.token?.id).toBe(token.id));
      context.setMode("read-failed");
      await act(async () => hook.result.current.onRetry());
      expect(hook.result.current.token?.name).toBe(token.name);
      expect(hook.result.current.error?.message).toBe("RAW_READ_FAILURE");
      expect(hook.result.current.loading).toBe(false);
    } finally {
      hook.unmount();
      context.client.stop();
    }
  });
  it.each(locales)(
    "%s detail dates use request time zone and stable hydration clock without leaking plaintext",
    async (locale) => {
      const errors = vi.fn(),
        recover = vi.fn(),
        root = document.createElement("div");
      document.body.appendChild(root);
      const ui = (
        <NextIntlClientProvider
          locale={locale}
          messages={catalogs[locale]}
          now={now}
          timeZone="America/Los_Angeles"
          onError={errors}
        >
          <TokenDetailScreen
            {...TOKEN_DETAIL}
            token={{ ...token, expiresAt: "2026-09-30T11:00:00Z" }}
          />
        </NextIntlClientProvider>
      );
      root.innerHTML = renderToString(ui);
      let hydrated!: ReturnType<typeof hydrateRoot>;
      try {
        await act(async () => {
          hydrated = hydrateRoot(root, ui, { onRecoverableError: recover });
        });
        const expected = new Intl.DateTimeFormat(locale, {
          year: "numeric",
          month: "short",
          day: "numeric",
          hour: "numeric",
          minute: "numeric",
          timeZoneName: "short",
          timeZone: "America/Los_Angeles",
        }).format(new Date(token.createdAt));
        expect(root.textContent).toContain(expected);
        expect(root.textContent).toContain(tFor(locale)("expired"));
        expect(root.textContent).toContain(token.scopes[0]);
        expect(root.textContent).toContain(token.tokenLast4);
        expect(root.textContent).not.toContain(plaintext);
        expect(recover).not.toHaveBeenCalled();
        expect(errors).not.toHaveBeenCalled();
      } finally {
        await act(async () => hydrated?.unmount());
        root.remove();
      }
    }
  );
  it("locale changes retain create drafts and key/scope identity; keyboard submits current translated form", async () => {
    const context = contextFor("en"),
      view = render(
        <NextIntlClientProvider locale="en" messages={catalogs.en} now={now} timeZone="UTC">
          <ApolloProvider client={context.client}>
            <Connected />
          </ApolloProvider>
        </NextIntlClientProvider>
      );
    try {
      await screen.findByText(token.name);
      fireEvent.click(screen.getByRole("button", { name: "New token" }));
      fireEvent.change(screen.getByLabelText("Name"), { target: { value: "UNCHANGED_DRAFT" } });
      view.rerender(
        <NextIntlClientProvider locale="ja" messages={catalogs.ja} now={now} timeZone="Asia/Tokyo">
          <ApolloProvider client={context.client}>
            <Connected />
          </ApolloProvider>
        </NextIntlClientProvider>
      );
      const input = screen.getByLabelText(tFor("ja")("name"));
      expect(input).toHaveValue("UNCHANGED_DRAFT");
      await userEvent.type(input, "{enter}");
      await screen.findByText(plaintext);
      expect(
        context.requests.find((r) => r.operationName === "CreateApiToken")!.variables
      ).toMatchObject({
        input: { name: "UNCHANGED_DRAFT", scopes: ["read:apps", "read:clusters", "mcp:read"] },
      });
    } finally {
      view.unmount();
      context.client.stop();
    }
  });
  it("an empty scope choice is refused without mutation and no clipboard value without creation", async () => {
    const context = contextFor("ko"),
      hook = renderHook(() => useTokens(), { wrapper: context.wrapper });
    try {
      await act(async () => {
        expect(
          await hook.result.current.onCreate({ name: "RAW", expiresInDays: "90", scopes: [] })
        ).toBe(false);
        await hook.result.current.onCopyPlaintext();
      });
      expect(context.requests.some((r) => r.operationName === "CreateApiToken")).toBe(false);
      expect(notifications.error).toHaveBeenCalledWith(tFor("ko")("pickScope"));
      expect(notifications.success).not.toHaveBeenCalled();
    } finally {
      hook.unmount();
      context.client.stop();
    }
  });
  it("read-only token list preserves gate, literal values and locale dates", async () => {
    notifications.allow = false;
    const context = contextFor("fr"),
      view = render(<Connected />, { wrapper: context.wrapper });
    try {
      await screen.findByText(token.name);
      expect(
        screen.queryByRole("button", { name: tFor("fr")("newToken") })
      ).not.toBeInTheDocument();
      expect(screen.getByText(token.scopes[0])).toBeInTheDocument();
      expect(
        context.requests.every(
          (r) => !r.operationName.includes("Create") && !r.operationName.includes("Revoke")
        )
      ).toBe(true);
      expect(context.errors).not.toHaveBeenCalled();
    } finally {
      view.unmount();
      context.client.stop();
    }
  });
  it.each(locales)(
    "%s actual revoke dialog retains raw refusal and closes only after acceptance",
    async (locale) => {
      const context = contextFor(locale, "refused"),
        t = tFor(locale),
        view = render(<Connected />, { wrapper: context.wrapper });
      try {
        await screen.findByText(token.name);
        const shared = createTranslator({
          locale,
          messages: catalogs[locale],
          namespace: "shared.list",
        });
        await userEvent.click(
          screen.getByRole("button", { name: shared("rowActions", { label: t("title") }) })
        );
        await userEvent.click(screen.getByRole("menuitem", { name: t("revoke") }));
        expect(screen.getByRole("alertdialog")).toHaveTextContent(
          t("revokeNamed", { name: token.name })
        );
        await userEvent.click(screen.getByRole("button", { name: t("revokeConfirm") }));
        await waitFor(() => expect(notifications.error).toHaveBeenCalledWith("RAW_POLICY_REFUSAL"));
        expect(screen.getByRole("alertdialog")).toBeInTheDocument();
        expect(notifications.success).not.toHaveBeenCalled();
        context.setMode("ok");
        await userEvent.click(screen.getByRole("button", { name: t("revokeConfirm") }));
        await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
        expect(notifications.success).toHaveBeenCalledWith(t("revokedFeedback"));
        expect(
          context.requests
            .filter((r) => r.operationName === "RevokeApiToken")
            .map((r) => r.variables)
        ).toEqual([{ input: { id: token.id } }, { input: { id: token.id } }]);
        expect(context.errors).not.toHaveBeenCalled();
      } finally {
        view.unmount();
        context.client.stop();
      }
    }
  );
  it("actual reveal remains selectable after browser copy denial; dismiss is explicit", async () => {
    const context = contextFor("fr"),
      t = tFor("fr"),
      view = render(<Connected />, { wrapper: context.wrapper });
    const descriptor = Object.getOwnPropertyDescriptor(navigator, "clipboard");
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: {
        writeText: vi.fn(async () => {
          throw new Error("CLIPBOARD_DENIED");
        }),
      },
    });
    try {
      await screen.findByText(token.name);
      await userEvent.click(screen.getByRole("button", { name: t("newToken") }));
      fireEvent.change(screen.getByLabelText(t("name")), { target: { value: "COPY_TARGET" } });
      await userEvent.click(screen.getByRole("button", { name: t("create") }));
      await screen.findByText(plaintext);
      await userEvent.click(screen.getByRole("button", { name: t("copy") }));
      expect(notifications.error).toHaveBeenCalledWith(t("copyFailed"));
      expect(notifications.success).not.toHaveBeenCalled();
      expect(screen.getByText(plaintext)).toBeInTheDocument();
      await userEvent.click(screen.getByRole("button", { name: t("dismissReveal") }));
      expect(screen.queryByText(plaintext)).not.toBeInTheDocument();
    } finally {
      view.unmount();
      context.client.stop();
      if (descriptor) Object.defineProperty(navigator, "clipboard", descriptor);
      else Reflect.deleteProperty(navigator, "clipboard");
    }
  });
  it.each(locales)(
    "%s scope read error shows translated frame and literal diagnostic",
    (locale) => {
      const errors = vi.fn();
      render(
        <NextIntlClientProvider
          locale={locale}
          messages={catalogs[locale]}
          now={now}
          timeZone="UTC"
          onError={errors}
        >
          <ScopePicker
            catalog={undefined}
            loading={false}
            error={new Error("RAW_SCOPE_READ_ERROR")}
            value={[]}
            onChange={() => {}}
          />
        </NextIntlClientProvider>
      );
      expect(screen.getByText(tFor(locale)("picker.loadFailed"))).toBeInTheDocument();
      expect(screen.getByText("RAW_SCOPE_READ_ERROR")).toBeInTheDocument();
      expect(errors).not.toHaveBeenCalled();
    }
  );
  it.each(locales)(
    "%s refused writes never refresh or warn about an unrelated failed read",
    async (locale) => {
      const context = contextFor(locale, "refused-refresh-failed"),
        hook = renderHook(() => useTokens(), { wrapper: context.wrapper });
      try {
        await waitFor(() => expect(hook.result.current.rows).toHaveLength(1));
        await act(async () => {
          expect(
            await hook.result.current.onCreate({
              name: "RAW_DRAFT",
              scopes: ["read:apps"],
              expiresInDays: "90",
            })
          ).toBe(false);
        });
        await act(async () => {
          await expect(hook.result.current.onRevoke(token)).rejects.toThrow("RAW_POLICY_REFUSAL");
        });
        expect(notifications.error).toHaveBeenCalledWith("RAW_POLICY_REFUSAL");
        expect(notifications.warning).not.toHaveBeenCalled();
        expect(notifications.success).not.toHaveBeenCalled();
        expect(
          context.requests.filter((r) => r.operationName === "ListApiTokensPage")
        ).toHaveLength(1);
        expect(hook.result.current.createdToken).toBeNull();
        expect(hook.result.current.rows[0].name).toBe(token.name);
      } finally {
        hook.unmount();
        context.client.stop();
      }
    }
  );
});
