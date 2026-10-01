import { readFileSync } from "node:fs";
import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
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
import { beforeEach, describe, expect, it, vi } from "vitest";

import { EnvironmentSettingsCard } from "@/app/(app)/apps/[slug]/settings/settings-client";
import { TooltipProvider } from "@/components/ui/tooltip";
import { LIST_ENVIRONMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import { PermissionsProvider } from "@/providers/PermissionsProvider";

const toast = vi.hoisted(() => ({ success: vi.fn(), warning: vi.fn(), error: vi.fn() }));
vi.mock("sonner", () => ({ toast }));
const locales = ["en", "es", "fr", "de", "ja", "ko", "zh-Hans", "pt-BR"];
const catalog = (locale: string) => JSON.parse(readFileSync(`messages/${locale}.json`, "utf8"));
const schema = buildSchema(readFileSync("../backend/schema.graphql", "utf8"));
const guid = (n: number) => `00000000-0000-0000-0000-${String(n).padStart(12, "0")}`;
const appSlug = "APP_LITERAL";
const envId = guid(1);
const existingKey = "FUTURE_KEY_LITERAL";
const existingValue = "provider/value_%literal";
const makeRows = () => [
  {
    id: envId,
    name: "ENV_A_LITERAL",
    registeredAppSlug: appSlug,
    clusterId: guid(3),
    clusterSlug: "CLUSTER_LITERAL",
    clusterProviderPluginSlug: "PROVIDER_LITERAL",
    createdAt: "2026-10-01T00:00:00Z",
    settings: [
      { id: guid(4), key: existingKey, value: existingValue },
      { id: guid(5), key: "cpu_limit", value: "" },
    ],
  },
  {
    id: guid(2),
    name: "ENV_B_LITERAL",
    registeredAppSlug: appSlug,
    clusterId: guid(3),
    clusterSlug: "CLUSTER_LITERAL",
    clusterProviderPluginSlug: "PROVIDER_LITERAL",
    createdAt: "2026-10-01T00:00:00Z",
    settings: [],
  },
];
type Mode =
  | "ok"
  | "refused"
  | "fallback"
  | "transport"
  | "refresh"
  | "read"
  | "pending"
  | "pending-refresh"
  | "unknown";
type Request = { query: string; operationName: string; variables: Record<string, unknown> };

function mount(locale: string, initial: Mode = "ok") {
  let mode = initial;
  const byApp = new Map<string, ReturnType<typeof makeRows>>([[appSlug, makeRows()]]);
  function appRows(targetSlug: string) {
    if (!byApp.has(targetSlug))
      byApp.set(
        targetSlug,
        makeRows().map((row, index) => ({
          ...row,
          id: guid(20 + index),
          registeredAppSlug: targetSlug,
        }))
      );
    return byApp.get(targetSlug)!;
  }
  let reads = 0;
  let permission = true;
  let activeLocale = locale;
  let slug = appSlug;
  let releaseMutation: (() => void) | undefined;
  let releaseRead: (() => void) | undefined;
  const requests: Request[] = [];
  const intlError = vi.fn();
  const t = createTranslator({
    locale,
    messages: catalog(locale),
    namespace: "apps.environmentOverrides",
  });
  const client = new ApolloClient({
    cache: new InMemoryCache(),
    link: new HttpLink({
      uri: "https://environment-settings.invalid/graphql/",
      fetch: async (_url, options) => {
        const request: Request = JSON.parse(String(options?.body));
        requests.push(request);
        const document = parse(request.query);
        expect(validate(schema, document)).toEqual([]);
        const read = request.operationName === "ListEnvironments";
        const readRows = read ? appRows(String(request.variables.appSlug)) : [];
        if (read) {
          reads++;
          if (mode === "unknown")
            return new Response(JSON.stringify({ data: { astroliftEnvironments: null } }), {
              status: 200,
              headers: { "Content-Type": "application/json" },
            });
          if (mode === "read" || (mode === "refresh" && reads > 1))
            throw new Error("RAW_ENV_READ_DIAGNOSTIC");
          if (mode === "pending-refresh" && reads > 1)
            await new Promise<void>((resolve) => {
              releaseRead = resolve;
            });
        } else {
          if (mode === "transport") throw new Error("RAW_ENV_TRANSPORT_DIAGNOSTIC");
          if (mode === "pending")
            await new Promise<void>((resolve) => {
              releaseMutation = resolve;
            });
        }
        const input = request.variables.input as
          | { environmentId: string; key: string; value?: string }
          | undefined;
        const refused = mode === "refused" || mode === "fallback";
        if (!read && !refused) {
          for (const [targetSlug, rows] of byApp)
            byApp.set(
              targetSlug,
              rows.map((row) =>
                row.id !== input!.environmentId
                  ? row
                  : {
                      ...row,
                      settings:
                        request.operationName === "ClearEnvironmentSetting"
                          ? row.settings.filter((s) => s.key !== input!.key)
                          : [
                              ...row.settings.filter((s) => s.key !== input!.key),
                              { id: guid(6), key: input!.key, value: input!.value! },
                            ],
                    }
              )
            );
        }
        const envelope = {
          ok: !refused,
          errors: refused
            ? [
                {
                  code: "PRECONDITION",
                  message: mode === "fallback" ? "" : "RAW_ENV_PROVIDER_REFUSAL",
                },
              ]
            : [],
          data: refused ? null : { id: guid(6), key: input?.key ?? "", value: input?.value ?? "" },
        };
        const result = await execute({
          schema,
          document,
          variableValues: request.variables,
          rootValue: {
            astroliftEnvironments: readRows,
            setEnvironmentSetting: envelope,
            clearEnvironmentSetting: envelope,
          },
          fieldResolver: (source, _args, _context, info) => {
            if (source && info.fieldName in source) return source[info.fieldName];
            if (!isNonNullType(info.returnType)) return null;
            const type = info.returnType.ofType;
            if (isListType(type)) return [];
            if (isEnumType(type)) return type.getValues()[0].value;
            if (isScalarType(type)) {
              if (type.name === "Boolean") return false;
              if (type.name === "Int" || type.name === "Float") return 0;
              if (type.name === "GUID") return guid(99);
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
  function Tree() {
    return (
      <NextIntlClientProvider
        locale={activeLocale}
        messages={catalog(activeLocale)}
        timeZone="UTC"
        onError={intlError}
      >
        <ApolloProvider client={client}>
          <PermissionsProvider
            value={{ loading: false, granted: new Set(permission ? ["app.update"] : []) }}
          >
            <TooltipProvider>
              <EnvironmentSettingsCard appSlug={slug} />
            </TooltipProvider>
          </PermissionsProvider>
        </ApolloProvider>
      </NextIntlClientProvider>
    );
  }
  const view = render(<Tree />);
  return {
    t,
    client,
    requests,
    intlError,
    setMode: (next: Mode) => {
      mode = next;
    },
    setRows: (next: ReturnType<typeof makeRows>) => {
      byApp.set(slug, next);
    },
    rows: () => appRows(slug),
    releaseMutation: () => releaseMutation?.(),
    releaseRead: () => releaseRead?.(),
    rerender: (options: { permission?: boolean; locale?: string; slug?: string }) => {
      permission = options.permission ?? permission;
      activeLocale = options.locale ?? activeLocale;
      slug = options.slug ?? slug;
      view.rerender(<Tree />);
    },
    refresh: () => client.refetchQueries({ include: [LIST_ENVIRONMENTS] }),
  };
}
const mutations = (ctx: ReturnType<typeof mount>) =>
  ctx.requests.filter((r) => r.operationName !== "ListEnvironments");
const reads = (ctx: ReturnType<typeof mount>) =>
  ctx.requests.filter((r) => r.operationName === "ListEnvironments");
async function ready(ctx: ReturnType<typeof mount>) {
  await screen.findByText(existingValue);
  expect(ctx.intlError).not.toHaveBeenCalled();
}
async function draft(
  ctx: ReturnType<typeof mount>,
  key = " NEW_KEY_LITERAL ",
  value = "RAW_VALUE_LITERAL"
) {
  await ready(ctx);
  fireEvent.change(screen.getByLabelText(ctx.t("keyLabel")), {
    target: { value: key },
  });
  fireEvent.change(screen.getByRole("textbox", { name: ctx.t("valueLabel") }), {
    target: { value },
  });
}
async function add(ctx: ReturnType<typeof mount>) {
  await userEvent.click(screen.getByRole("button", { name: ctx.t("add") }));
}
async function clear(ctx: ReturnType<typeof mount>) {
  await ready(ctx);
  await userEvent.click(
    screen.getByRole("button", { name: ctx.t("clearLabel", { key: existingKey }) })
  );
}
beforeEach(() => vi.resetAllMocks());

describe.each(locales)("Connected environment override card in %s", (locale) => {
  it("localizes human controls while retaining unknown keys, values and suggested tokens", async () => {
    const ctx = mount(locale);
    await ready(ctx);
    expect(screen.getByText(ctx.t("title"))).toBeInTheDocument();
    expect(screen.getByText(existingKey)).toBeInTheDocument();
    expect(screen.getByText(ctx.t("emptyValue"))).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "cpu_limit" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "cpu_request" }));
    expect(screen.getByLabelText(ctx.t("keyLabel"))).toHaveValue("cpu_request");
    expect(mutations(ctx)).toHaveLength(0);
  });
  it("validates trimmed keys, submits the exact existing input by keyboard and clears accepted inputs", async () => {
    const ctx = mount(locale);
    await draft(ctx, "   ");
    await add(ctx);
    expect(toast.error).toHaveBeenCalledWith(ctx.t("keyRequired"));
    expect(mutations(ctx)).toHaveLength(0);
    await draft(ctx);
    await userEvent.click(screen.getByRole("textbox", { name: ctx.t("valueLabel") }));
    await userEvent.keyboard("{Enter}");
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith(ctx.t("saved", { key: "NEW_KEY_LITERAL" }))
    );
    await waitFor(() => expect(screen.getByLabelText(ctx.t("keyLabel"))).toHaveValue(""));
    expect(mutations(ctx).map((r) => [r.operationName, r.variables])).toEqual([
      [
        "SetEnvironmentSetting",
        { input: { environmentId: envId, key: "NEW_KEY_LITERAL", value: "RAW_VALUE_LITERAL" } },
      ],
    ]);
    expect(reads(ctx).map((r) => r.variables)).toEqual([{ appSlug }, { appSlug }]);
    expect(toast.warning).not.toHaveBeenCalled();
    expect(ctx.intlError).not.toHaveBeenCalled();
  });
  it.each(["refused", "fallback", "transport"] as const)(
    "add %s retains drafts, performs no refresh, and retries the unchanged input",
    async (mode) => {
      const ctx = mount(locale, mode);
      await draft(ctx);
      await add(ctx);
      await waitFor(() =>
        expect(toast.error).toHaveBeenCalledWith(
          mode === "refused"
            ? "RAW_ENV_PROVIDER_REFUSAL"
            : mode === "transport"
              ? "RAW_ENV_TRANSPORT_DIAGNOSTIC"
              : ctx.t("saveFailed")
        )
      );
      expect(screen.getByLabelText(ctx.t("keyLabel"))).toHaveValue(" NEW_KEY_LITERAL ");
      expect(screen.getByRole("textbox", { name: ctx.t("valueLabel") })).toHaveValue(
        "RAW_VALUE_LITERAL"
      );
      expect(reads(ctx)).toHaveLength(1);
      expect(toast.success).not.toHaveBeenCalled();
      ctx.setMode("ok");
      await add(ctx);
      await waitFor(() => expect(screen.getByLabelText(ctx.t("keyLabel"))).toHaveValue(""));
      expect(mutations(ctx)[0].variables).toEqual(mutations(ctx)[1].variables);
    }
  );
  it.each(["refused", "fallback", "transport"] as const)(
    "clear %s retains rows, performs no refresh and releases pending admission",
    async (mode) => {
      const ctx = mount(locale, mode);
      await clear(ctx);
      await waitFor(() =>
        expect(toast.error).toHaveBeenCalledWith(
          mode === "refused"
            ? "RAW_ENV_PROVIDER_REFUSAL"
            : mode === "transport"
              ? "RAW_ENV_TRANSPORT_DIAGNOSTIC"
              : ctx.t("clearFailed")
        )
      );
      expect(screen.getByText(existingValue)).toBeInTheDocument();
      expect(reads(ctx)).toHaveLength(1);
      ctx.setMode("ok");
      await clear(ctx);
      await waitFor(() => expect(screen.queryByText(existingValue)).toBeNull());
      expect(mutations(ctx).map((r) => r.variables)).toEqual([
        { input: { environmentId: envId, key: existingKey } },
        { input: { environmentId: envId, key: existingKey } },
      ]);
      expect(toast.success).toHaveBeenCalledWith(ctx.t("cleared", { key: existingKey }));
    }
  );
  it.each(["add", "clear"] as const)(
    "accepted %s survives failed refresh and shows cached rows, diagnostic and real retry",
    async (operation) => {
      const ctx = mount(locale, "refresh");
      if (operation === "add") {
        await draft(ctx);
        await add(ctx);
      } else await clear(ctx);
      await waitFor(() =>
        expect(toast.warning).toHaveBeenCalledWith(ctx.t("refreshWarning"), {
          description: "RAW_ENV_READ_DIAGNOSTIC",
        })
      );
      expect(toast.success).toHaveBeenCalledTimes(1);
      expect(toast.error).not.toHaveBeenCalled();
      expect(mutations(ctx)).toHaveLength(1);
      expect(screen.getByRole("alert")).toHaveTextContent("RAW_ENV_READ_DIAGNOSTIC");
      expect(screen.getByText(existingValue)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: ctx.t("add") })).toBeDisabled();
      if (operation === "add") expect(screen.getByLabelText(ctx.t("keyLabel"))).toHaveValue("");
      ctx.setMode("ok");
      await userEvent.click(screen.getByRole("button", { name: ctx.t("retry") }));
      await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
      expect(mutations(ctx)).toHaveLength(1);
      expect(ctx.intlError).not.toHaveBeenCalled();
    }
  );
  it("distinguishes failed first read from confirmed empty environments", async () => {
    const ctx = mount(locale, "read");
    expect(await screen.findByRole("alert")).toHaveTextContent(ctx.t("readFailed"));
    expect(screen.getByRole("button", { name: ctx.t("add") })).toBeDisabled();
    ctx.setMode("ok");
    ctx.setRows([]);
    await userEvent.click(screen.getByRole("button", { name: ctx.t("retry") }));
    await waitFor(() => expect(screen.queryByText(ctx.t("title"))).toBeNull());
    expect(mutations(ctx)).toHaveLength(0);
  });
  it("treats an invalid null inventory as unavailable rather than confirmed empty", async () => {
    const ctx = mount(locale, "unknown");
    expect(await screen.findByRole("alert")).toHaveTextContent(ctx.t("readFailed"));
    expect(screen.getByRole("button", { name: ctx.t("add") })).toBeDisabled();
    ctx.setMode("ok");
    await userEvent.click(screen.getByRole("button", { name: ctx.t("retry") }));
    await ready(ctx);
    expect(screen.queryByRole("alert")).toBeNull();
    expect(mutations(ctx)).toHaveLength(0);
  });
  it("preserves a newly edited draft while an earlier accepted save completes", async () => {
    const ctx = mount(locale, "pending");
    await draft(ctx);
    await add(ctx);
    await waitFor(() => expect(mutations(ctx)).toHaveLength(1));
    expect(screen.getByRole("button", { name: ctx.t("add") })).toBeDisabled();
    fireEvent.change(screen.getByLabelText(ctx.t("keyLabel")), {
      target: { value: "NEXT_KEY_LITERAL" },
    });
    await act(async () => ctx.releaseMutation());
    await waitFor(() => expect(toast.success).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByRole("button", { name: ctx.t("add") })).toBeEnabled());
    expect(screen.getByLabelText(ctx.t("keyLabel"))).toHaveValue("NEXT_KEY_LITERAL");
    expect(mutations(ctx)).toHaveLength(1);
  });
  it("retains per-row pending admission until an accepted clear's read settles", async () => {
    const ctx = mount(locale, "pending-refresh");
    await clear(ctx);
    const button = screen.getByRole("button", { name: ctx.t("clearLabel", { key: existingKey }) });
    await waitFor(() => expect(reads(ctx)).toHaveLength(2));
    expect(button).toBeDisabled();
    await userEvent.click(button);
    expect(mutations(ctx)).toHaveLength(1);
    expect(
      screen.getByRole("button", { name: ctx.t("clearLabel", { key: "cpu_limit" }) })
    ).toBeEnabled();
    await act(async () => ctx.releaseRead());
    await waitFor(() => expect(screen.queryByText(existingValue)).toBeNull());
  });
  it("invalidates observed cluster-source ABA drafts without reviving the old values", async () => {
    const ctx = mount(locale);
    await draft(ctx);
    const original = ctx.rows();
    ctx.setRows(original.map((row) => ({ ...row, clusterId: guid(88) })));
    await act(async () => ctx.refresh());
    await waitFor(() => expect(screen.getByLabelText(ctx.t("keyLabel"))).toHaveValue(""));
    fireEvent.change(screen.getByLabelText(ctx.t("keyLabel")), {
      target: { value: "B_REVIEW_LITERAL" },
    });
    ctx.setRows(original);
    await act(async () => ctx.refresh());
    await waitFor(() => expect(screen.getByLabelText(ctx.t("keyLabel"))).toHaveValue(""));
    expect(screen.getByRole("textbox", { name: ctx.t("valueLabel") })).toHaveValue("");
    expect(mutations(ctx)).toHaveLength(0);
  });
  it("invalidates permission withdrawal and does not revive drafts when it returns", async () => {
    const ctx = mount(locale);
    await draft(ctx);
    ctx.rerender({ permission: false });
    expect(screen.queryByRole("button", { name: ctx.t("add") })).toBeNull();
    expect(
      screen.queryByRole("button", { name: ctx.t("clearLabel", { key: existingKey }) })
    ).toBeNull();
    ctx.rerender({ permission: true });
    expect(screen.getByLabelText(ctx.t("keyLabel"))).toHaveValue("");
    expect(mutations(ctx)).toHaveLength(0);
  });
  it("clears environment-selection ABA drafts and localizes the empty state with its literal name", async () => {
    const ctx = mount(locale);
    await draft(ctx);
    await userEvent.click(screen.getByRole("combobox", { name: ctx.t("environment") }));
    await userEvent.click(await screen.findByRole("option", { name: "ENV_B_LITERAL" }));
    expect(screen.getByLabelText(ctx.t("keyLabel"))).toHaveValue("");
    expect(screen.getByText("ENV_B_LITERAL", { selector: "span.font-mono" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(ctx.t("keyLabel")), {
      target: { value: "B_REVIEW_LITERAL" },
    });
    await userEvent.click(screen.getByRole("combobox", { name: ctx.t("environment") }));
    await userEvent.click(await screen.findByRole("option", { name: "ENV_A_LITERAL" }));
    expect(screen.getByLabelText(ctx.t("keyLabel"))).toHaveValue("");
    expect(mutations(ctx)).toHaveLength(0);
    expect(ctx.intlError).not.toHaveBeenCalled();
  });
  it("invalidates confirmed source withdrawal without reviving the removed draft", async () => {
    const ctx = mount(locale);
    await draft(ctx);
    const original = ctx.rows();
    ctx.setRows([]);
    await act(async () => ctx.refresh());
    await waitFor(() => expect(screen.queryByText(ctx.t("title"))).toBeNull());
    ctx.setRows(original);
    await act(async () => ctx.refresh());
    expect(await screen.findByLabelText(ctx.t("keyLabel"))).toHaveValue("");
    expect(mutations(ctx)).toHaveLength(0);
  });
  it("ignores an old accepted callback for a new app's draft and skips its obsolete refresh", async () => {
    const ctx = mount(locale, "pending");
    await draft(ctx);
    await add(ctx);
    await waitFor(() => expect(mutations(ctx)).toHaveLength(1));
    ctx.rerender({ slug: "OTHER_APP_LITERAL" });
    await ready(ctx);
    expect(screen.getByLabelText(ctx.t("keyLabel"))).toHaveValue("");
    fireEvent.change(screen.getByLabelText(ctx.t("keyLabel")), {
      target: { value: "OTHER_APP_DRAFT_LITERAL" },
    });
    await act(async () => ctx.releaseMutation());
    await waitFor(() => expect(toast.success).toHaveBeenCalledTimes(1));
    expect(screen.getByLabelText(ctx.t("keyLabel"))).toHaveValue("OTHER_APP_DRAFT_LITERAL");
    expect(reads(ctx).map((r) => r.variables)).toEqual([
      { appSlug },
      { appSlug: "OTHER_APP_LITERAL" },
    ]);
    ctx.rerender({ slug: appSlug });
    await ready(ctx);
    expect(screen.getByLabelText(ctx.t("keyLabel"))).toHaveValue("");
  });
  it("retains cached drafts after a failed read and recovers only through a successful actual retry", async () => {
    const ctx = mount(locale);
    await draft(ctx);
    ctx.setMode("read");
    await act(async () => {
      await ctx.refresh().catch(() => {});
    });
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("RAW_ENV_READ_DIAGNOSTIC")
    );
    expect(screen.getByLabelText(ctx.t("keyLabel"))).toHaveValue(" NEW_KEY_LITERAL ");
    expect(screen.getByRole("button", { name: ctx.t("add") })).toBeDisabled();
    ctx.setMode("pending-refresh");
    await userEvent.click(screen.getByRole("button", { name: ctx.t("retry") }));
    await waitFor(() => expect(reads(ctx)).toHaveLength(3));
    expect(screen.getByRole("alert")).toHaveTextContent("RAW_ENV_READ_DIAGNOSTIC");
    expect(screen.getByRole("button", { name: ctx.t("add") })).toBeDisabled();
    await act(async () => ctx.releaseRead());
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
    expect(screen.getByLabelText(ctx.t("keyLabel"))).toHaveValue(" NEW_KEY_LITERAL ");
    expect(mutations(ctx)).toHaveLength(0);
  });
  it("retains drafts across language changes while keeping technical values literal", async () => {
    const ctx = mount(locale);
    await draft(ctx);
    const nextLocale = locale === "en" ? "es" : "en";
    ctx.rerender({ locale: nextLocale });
    const next = createTranslator({
      locale: nextLocale,
      messages: catalog(nextLocale),
      namespace: "apps.environmentOverrides",
    });
    expect(screen.getByLabelText(next("keyLabel"))).toHaveValue(" NEW_KEY_LITERAL ");
    expect(ctx.intlError).not.toHaveBeenCalled();
  });
});
