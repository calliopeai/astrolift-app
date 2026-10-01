import { readFileSync } from "node:fs";
import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import {
  buildSchema,
  execute,
  isEnumType,
  isListType,
  isNonNullType,
  isScalarType,
  parse,
  validate,
} from "graphql";
import { createTranslator, NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AppFrameContainer } from "@/app/(app)/apps/[slug]/components/app-frame";
import { PermissionsProvider } from "@/providers/PermissionsProvider";

const state = vi.hoisted(() => ({
  push: vi.fn(),
  success: vi.fn(),
  warning: vi.fn(),
  error: vi.fn(),
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: state.push }),
  usePathname: () => "/apps/slug-literal-123",
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("sonner", () => ({ toast: state }));

const locales = ["en", "es", "fr", "de", "ja", "ko", "zh-Hans", "pt-BR"];
const catalog = (locale: string) => JSON.parse(readFileSync(`messages/${locale}.json`, "utf8"));
const schema = buildSchema(readFileSync("../backend/schema.graphql", "utf8"));
const slug = "slug-literal-123";
const id = "00000000-0000-0000-0000-000000000123";
type Mode =
  | "ok"
  | "refused"
  | "fallback"
  | "transport"
  | "refresh"
  | "navigation"
  | "both"
  | "pending";
type Request = { query: string; operationName: string; variables: Record<string, unknown> };

function mount(locale: string, initial: Mode = "ok", permission = true) {
  let mode = initial;
  let release: (() => void) | undefined;
  const requests: Request[] = [];
  const messages = catalog(locale);
  const intlError = vi.fn();
  const t = createTranslator({ locale, messages, namespace: "apps.frame" });
  if (mode === "navigation" || mode === "both")
    state.push.mockImplementation(() => {
      throw new Error("RAW_NAVIGATION_DIAGNOSTIC");
    });
  const client = new ApolloClient({
    cache: new InMemoryCache(),
    link: new HttpLink({
      uri: "https://app-frame-delete.invalid/graphql/",
      fetch: async (_url, options) => {
        const request: Request = JSON.parse(String(options?.body));
        requests.push(request);
        const document = parse(request.query);
        expect(validate(schema, document)).toEqual([]);
        if (request.operationName === "SoftDeleteApp" && mode === "transport")
          throw new Error("RAW_PROVIDER_TRANSPORT_DIAGNOSTIC");
        if (request.operationName === "SoftDeleteApp" && mode === "pending")
          await new Promise<void>((resolve) => {
            release = resolve;
          });
        if (request.operationName === "ListApps" && (mode === "refresh" || mode === "both"))
          throw new Error("RAW_LIST_REFRESH_DIAGNOSTIC");
        const refused = mode === "refused" || mode === "fallback";
        const root = {
          astroliftApp: {
            id,
            slug,
            name: "NAME_LITERAL",
            provisioningStatus: "ready",
            isArchived: false,
            isActive: true,
          },
          astroliftWorkloads: [],
          astroliftEnvironments: [],
          astroliftDeployments: [],
          astroliftApps: [],
          softDeleteApp: {
            ok: !refused,
            errors: refused
              ? [
                  {
                    code: "PRECONDITION",
                    message: mode === "refused" ? "RAW_PROVIDER_REFUSAL" : "",
                  },
                ]
              : [],
            data: refused ? null : { id, deleted: true },
          },
        };
        const result = await execute({
          schema,
          document,
          rootValue: root,
          variableValues: request.variables,
          fieldResolver: (source, _args, _context, info) => {
            if (source && info.fieldName in source) return source[info.fieldName];
            if (!isNonNullType(info.returnType)) return null;
            const type = info.returnType.ofType;
            if (isListType(type)) return [];
            if (isEnumType(type)) return type.getValues()[0].value;
            if (isScalarType(type)) {
              if (type.name === "Boolean") return false;
              if (type.name === "Int" || type.name === "Float") return 0;
              if (type.name === "GUID") return id;
              if (type.name === "DateTime") return "2026-10-01T00:00:00Z";
              return "";
            }
            return {};
          },
        });
        expect(result.errors).toBeUndefined();
        return new Response(JSON.stringify(result), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      },
    }),
  });
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <NextIntlClientProvider locale={locale} messages={messages} timeZone="UTC" onError={intlError}>
      <ApolloProvider client={client}>
        <PermissionsProvider
          value={{ loading: false, granted: new Set(permission ? ["app.delete"] : []) }}
        >
          {children}
        </PermissionsProvider>
      </ApolloProvider>
    </NextIntlClientProvider>
  );
  const rendered = render(<AppFrameContainer slug={slug}>BODY_LITERAL</AppFrameContainer>, {
    wrapper: Wrapper,
  });
  return {
    requests,
    locale,
    client,
    t,
    messages,
    intlError,
    rendered,
    setMode: (next: Mode) => {
      mode = next;
    },
    release: () => release?.(),
  };
}

async function open(ctx: ReturnType<typeof mount>) {
  await screen.findByTitle("NAME_LITERAL");
  await userEvent.click(screen.getByRole("button", { name: ctx.t("menu.label") }));
  await userEvent.click(await screen.findByRole("menuitem", { name: ctx.t("menu.delete") }));
  return screen.findByRole("alertdialog");
}
async function confirm(ctx: ReturnType<typeof mount>) {
  const dialog = await open(ctx);
  await userEvent.click(
    within(dialog).getByRole("button", {
      name: createTranslator({
        locale: ctx.locale,
        messages: ctx.messages,
        namespace: "apps.detail.delete",
      })("confirm"),
    })
  );
}
function mutations(ctx: ReturnType<typeof mount>) {
  return ctx.requests.filter((request) => request.operationName === "SoftDeleteApp");
}
beforeEach(() => {
  vi.resetAllMocks();
  localStorage.clear();
});

describe.each(locales)("Connected app-frame deletion in %s", (locale) => {
  it("requires the actual confirmation and sends the existing literal ID input", async () => {
    const ctx = mount(locale);
    const dialog = await open(ctx);
    expect(dialog).toHaveTextContent(slug);
    expect(mutations(ctx)).toHaveLength(0);
    const t = createTranslator({ locale, messages: ctx.messages, namespace: "apps.detail.delete" });
    await userEvent.click(within(dialog).getByRole("button", { name: t("confirm") }));
    await waitFor(() =>
      expect(state.success).toHaveBeenCalledWith(ctx.t("deleteFeedback.deleted", { slug }))
    );
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    expect(mutations(ctx).map((r) => r.variables)).toEqual([{ input: { id } }]);
    expect(ctx.requests.filter((r) => r.operationName === "ListApps")).toHaveLength(1);
    expect(state.push).toHaveBeenCalledExactlyOnceWith("/apps");
    expect(state.warning).not.toHaveBeenCalled();
    expect(state.error).not.toHaveBeenCalled();
    expect(ctx.intlError).not.toHaveBeenCalled();
  });
  it.each(["refused", "fallback", "transport"] as const)(
    "%s preserves refusal, no list refresh/navigation, and permits retry",
    async (mode) => {
      const ctx = mount(locale, mode);
      await confirm(ctx);
      const expected =
        mode === "refused"
          ? "RAW_PROVIDER_REFUSAL"
          : mode === "transport"
            ? "RAW_PROVIDER_TRANSPORT_DIAGNOSTIC"
            : ctx.t("deleteFeedback.failed");
      await waitFor(() => expect(state.error).toHaveBeenCalledWith(expected));
      expect(screen.getByRole("alertdialog")).toBeInTheDocument();
      expect(state.success).not.toHaveBeenCalled();
      expect(state.push).not.toHaveBeenCalled();
      expect(ctx.requests.filter((r) => r.operationName === "ListApps")).toHaveLength(0);
      ctx.setMode("ok");
      const t = createTranslator({
        locale,
        messages: ctx.messages,
        namespace: "apps.detail.delete",
      });
      await userEvent.click(
        within(screen.getByRole("alertdialog")).getByRole("button", { name: t("confirm") })
      );
      await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
      expect(mutations(ctx)).toHaveLength(2);
      expect(state.success).toHaveBeenCalledWith(ctx.t("deleteFeedback.deleted", { slug }));
      expect(ctx.intlError).not.toHaveBeenCalled();
    }
  );
  it.each(["refresh", "navigation", "both"] as const)(
    "%s failure retains accepted deletion and closes without another delete",
    async (mode) => {
      const ctx = mount(locale, mode);
      await confirm(ctx);
      await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
      expect(state.success).toHaveBeenCalledExactlyOnceWith(
        ctx.t("deleteFeedback.deleted", { slug })
      );
      expect(state.error).not.toHaveBeenCalled();
      expect(mutations(ctx)).toHaveLength(1);
      expect(state.push).toHaveBeenCalledExactlyOnceWith("/apps");
      if (mode !== "navigation")
        expect(state.warning).toHaveBeenCalledWith(
          ctx.t("deleteFeedback.refreshWarning", { slug }),
          { description: "RAW_LIST_REFRESH_DIAGNOSTIC" }
        );
      if (mode !== "refresh")
        expect(state.warning).toHaveBeenCalledWith(
          ctx.t("deleteFeedback.navigationWarning", { slug }),
          { description: "RAW_NAVIGATION_DIAGNOSTIC" }
        );
      expect(ctx.intlError).not.toHaveBeenCalled();
    }
  );
  it("keeps confirmation pending until the provider response without duplicate effects", async () => {
    const ctx = mount(locale, "pending");
    await confirm(ctx);
    await waitFor(() => expect(mutations(ctx)).toHaveLength(1));
    expect(
      within(screen.getByRole("alertdialog"))
        .getAllByRole("button")
        .every((b) => (b as HTMLButtonElement).disabled)
    ).toBe(true);
    expect(state.success).not.toHaveBeenCalled();
    expect(state.push).not.toHaveBeenCalled();
    await act(async () => ctx.release());
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    expect(mutations(ctx)).toHaveLength(1);
    expect(state.success).toHaveBeenCalledWith(ctx.t("deleteFeedback.deleted", { slug }));
  });
  it("retains the real app.delete visibility gate", async () => {
    const ctx = mount(locale, "ok", false);
    await screen.findByTitle("NAME_LITERAL");
    await userEvent.click(screen.getByRole("button", { name: ctx.t("menu.label") }));
    expect(screen.queryByRole("menuitem", { name: ctx.t("menu.delete") })).toBeNull();
    expect(mutations(ctx)).toHaveLength(0);
  });
});
