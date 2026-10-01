import { readFileSync } from "node:fs";
import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { userEvent } from "@testing-library/user-event";
import {
  buildSchema,
  execute,
  isEnumType,
  isListType,
  isNonNullType,
  isScalarType,
  parse,
  print,
  validate,
  visit,
} from "graphql";
import { createTranslator, NextIntlClientProvider } from "next-intl";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ManagedServicesAdminCard } from "@/app/(app)/apps/[slug]/settings/settings-client";
import { TooltipProvider } from "@/components/ui/tooltip";
import {
  REPROVISION_MANAGED_SERVICE,
  UPDATE_MANAGED_SERVICE,
} from "@/graphql/services/services.mutations";
import { LIST_MANAGED_SERVICES } from "@/graphql/services/services.queries";
import { PermissionsProvider } from "@/providers/PermissionsProvider";

const toast = vi.hoisted(() => ({
  success: vi.fn(),
  warning: vi.fn(),
  error: vi.fn(),
  message: vi.fn(),
}));
vi.mock("sonner", () => ({ toast }));
const locales = ["en", "es", "fr", "de", "ja", "ko", "zh-Hans", "pt-BR"];
const catalog = (locale: string) => JSON.parse(readFileSync(`messages/${locale}.json`, "utf8"));
const schema = buildSchema(readFileSync("../backend/schema.graphql", "utf8"));
const guid = (n: number) => `00000000-0000-0000-0000-${String(n).padStart(12, "0")}`;
const appSlug = "APP_LITERAL";
const serviceId = guid(1);
const name = "NAME_LITERAL";
const makeRows = () => [
  {
    id: serviceId,
    name,
    kind: "KIND_LITERAL",
    variant: "VARIANT_LITERAL",
    environmentName: "ENV_LITERAL",
    registeredAppSlug: appSlug,
    createdAt: "2026-10-01T00:00:00Z",
    updatedAt: "2026-10-01T00:00:00Z",
    status: "active",
    statusError: "RAW_STATUS_DIAGNOSTIC",
    config: {
      capacity: 4,
      enabled: true,
      text: "TEXT_LITERAL",
      locked: { nested: [1, false, null, "NESTED_LITERAL"] },
    },
    appliedConfig: null,
    editableFields: ["capacity", "enabled", "text"],
    operationKind: "",
    operationWorkflowId: "",
    operationRunId: "",
  },
];
type Rows = ReturnType<typeof makeRows>;
type Mode =
  | "ok"
  | "refused"
  | "fallback"
  | "transport"
  | "read"
  | "refresh"
  | "unknown"
  | "pending"
  | "pending-refresh";
type Request = { query: string; operationName: string; variables: Record<string, unknown> };
function mount(locale: string, initial: Mode = "ok", initialRows = makeRows()) {
  let mode = initial;
  let rows = initialRows;
  let reads = 0;
  let permission = "app.deploy";
  let slug = appSlug;
  let activeLocale = locale;
  let releaseMutation: (() => void) | undefined;
  let releaseRead: (() => void) | undefined;
  const requests: Request[] = [];
  const intlError = vi.fn();
  const t = createTranslator({
    locale,
    messages: catalog(locale),
    namespace: "apps.managedServicesAdmin",
  });
  const client = new ApolloClient({
    cache: new InMemoryCache(),
    link: new HttpLink({
      uri: "https://managed-admin.invalid/graphql/",
      fetch: async (_url, options) => {
        const request: Request = JSON.parse(String(options?.body));
        requests.push(request);
        const document = parse(request.query);
        expect(validate(schema, document)).toEqual([]);
        const read = request.operationName === "ListManagedServices";
        expect(
          print(
            visit(document, {
              Field: (node) => (node.name.value === "__typename" ? null : undefined),
            })
          )
        ).toBe(
          print(
            read
              ? LIST_MANAGED_SERVICES
              : request.operationName === "UpdateManagedService"
                ? UPDATE_MANAGED_SERVICE
                : REPROVISION_MANAGED_SERVICE
          )
        );
        if (read) {
          reads++;
          if (mode === "unknown")
            return new Response(JSON.stringify({ data: { astroliftManagedServices: null } }), {
              headers: { "Content-Type": "application/json" },
            });
          if (mode === "read" || (mode === "refresh" && reads > 1))
            throw new Error("RAW_MANAGED_READ_DIAGNOSTIC");
          if (mode === "pending-refresh" && reads > 1)
            await new Promise<void>((resolve) => {
              releaseRead = resolve;
            });
        } else {
          if (mode === "transport") throw new Error("RAW_MANAGED_TRANSPORT_DIAGNOSTIC");
          if (mode === "pending")
            await new Promise<void>((resolve) => {
              releaseMutation = resolve;
            });
        }
        const input = request.variables.input as
          | { id?: string; managedServiceId?: string; config?: Rows[0]["config"] }
          | undefined;
        const refused = mode === "refused" || mode === "fallback";
        const resultService = input
          ? (rows.find((row) => row.id === (input.id ?? input.managedServiceId)) ?? makeRows()[0])
          : rows[0];
        if (!read && !refused && slug === appSlug)
          rows = rows.map((row) =>
            row.id === resultService.id
              ? {
                  ...row,
                  config: input!.config ?? row.config,
                  status: input!.id ? "updating" : "pending",
                }
              : row
          );
        const envelope = {
          ok: !refused,
          errors: refused
            ? [
                {
                  code: "VALIDATION",
                  message: mode === "fallback" ? "" : "RAW_PROVIDER_UNSUPPORTED_REFUSAL",
                },
              ]
            : [],
          data: refused
            ? null
            : {
                ...resultService,
                config: input?.config ?? resultService?.config,
                status: input?.id ? "updating" : "pending",
              },
        };
        const result = await execute({
          schema,
          document,
          variableValues: request.variables,
          rootValue: {
            astroliftManagedServices: rows,
            updateManagedService: envelope,
            reprovisionManagedService: envelope,
          },
          fieldResolver: (source, _args, _context, info) => {
            if (source && Object.hasOwn(source, info.fieldName)) return source[info.fieldName];
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
            value={{ loading: false, granted: new Set(permission ? [permission] : []) }}
          >
            <TooltipProvider>
              <ManagedServicesAdminCard appSlug={slug} />
            </TooltipProvider>
          </PermissionsProvider>
        </ApolloProvider>
      </NextIntlClientProvider>
    );
  }
  const view = render(<Tree />);
  return {
    t,
    requests,
    intlError,
    client,
    rows: () => rows,
    setRows: (next: Rows) => {
      rows = next;
    },
    setMode: (next: Mode) => {
      mode = next;
    },
    refresh: () => client.refetchQueries({ include: [LIST_MANAGED_SERVICES] }),
    releaseMutation: () => releaseMutation?.(),
    releaseRead: () => releaseRead?.(),
    rerender: (options: { permission?: string; slug?: string; locale?: string }) => {
      permission = options.permission ?? permission;
      slug = options.slug ?? slug;
      activeLocale = options.locale ?? activeLocale;
      view.rerender(<Tree />);
    },
  };
}
const mutations = (ctx: ReturnType<typeof mount>) =>
  ctx.requests.filter((r) => r.operationName !== "ListManagedServices");
const reads = (ctx: ReturnType<typeof mount>) =>
  ctx.requests.filter((r) => r.operationName === "ListManagedServices");
async function ready(ctx: ReturnType<typeof mount>) {
  await screen.findByText(name);
  expect(ctx.intlError).not.toHaveBeenCalled();
}
async function edit(ctx: ReturnType<typeof mount>, field = "capacity", value = "8") {
  await ready(ctx);
  await userEvent.click(screen.getByRole("button", { name: new RegExp(`^${ctx.t("edit")}`) }));
  await screen.findByRole("dialog");
  fireEvent.change(screen.getByLabelText(field), { target: { value } });
}
async function submit(ctx: ReturnType<typeof mount>, operation: "update" | "reprovision") {
  if (operation === "update")
    await userEvent.click(screen.getByRole("button", { name: ctx.t("save") }));
  else {
    await ready(ctx);
    await userEvent.click(screen.getByRole("button", { name: ctx.t("reprovision") }));
    await userEvent.click(
      within(screen.getByRole("alertdialog")).getByRole("button", { name: ctx.t("reprovision") })
    );
  }
}
beforeEach(() => vi.resetAllMocks());
describe.each(locales)("Actual managed-services settings card in %s", (locale) => {
  it("translates known states and controls while retaining technical identifiers and raw diagnostics", async () => {
    const ctx = mount(locale);
    await ready(ctx);
    expect(screen.getByText(ctx.t("title"))).toBeInTheDocument();
    expect(screen.getByText(ctx.t("status.active"))).toBeInTheDocument();
    expect(screen.getByText("KIND_LITERAL/VARIANT_LITERAL")).toBeInTheDocument();
    expect(screen.getByText("ENV_LITERAL")).toBeInTheDocument();
    expect(screen.getByText("RAW_STATUS_DIAGNOSTIC")).toBeInTheDocument();
    const unknown = ["constructor", "__proto__", "FUTURE_STATUS_LITERAL"];
    for (const status of unknown) {
      ctx.setRows(makeRows().map((row) => ({ ...row, status })));
      await act(async () => ctx.refresh());
      expect(screen.getByText(status)).toBeInTheDocument();
    }
    expect(mutations(ctx)).toHaveLength(0);
  });
  it("sends exactly full captured config without losing locked typed fields", async () => {
    const ctx = mount(locale);
    await edit(ctx);
    await submit(ctx, "update");
    await waitFor(() =>
      expect(toast.success).toHaveBeenCalledWith(ctx.t("updateAccepted", { name }))
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(mutations(ctx).map((r) => [r.operationName, r.variables])).toEqual([
      [
        "UpdateManagedService",
        { input: { id: serviceId, config: { ...makeRows()[0].config, capacity: 8 } } },
      ],
    ]);
    expect(reads(ctx).map((r) => r.variables)).toEqual([
      { appSlug, environmentName: null },
      { appSlug, environmentName: null },
    ]);
  });
  it("round-trips number, explicit boolean, raw text and unchanged nested JSON through wildcard existing keys", async () => {
    const ctx = mount(
      locale,
      "ok",
      makeRows().map((row) => ({ ...row, editableFields: ["*"] }))
    );
    await edit(ctx, "capacity", "-2.5e2");
    fireEvent.change(screen.getByLabelText("enabled"), { target: { value: "0" } });
    fireEvent.change(screen.getByLabelText("text"), { target: { value: " RAW_TEXT_LITERAL " } });
    expect(screen.getByLabelText("locked")).toHaveValue(
      JSON.stringify(makeRows()[0].config.locked)
    );
    expect(screen.queryByLabelText("*")).toBeNull();
    await submit(ctx, "update");
    await waitFor(() => expect(toast.success).toHaveBeenCalledTimes(1));
    expect(mutations(ctx)[0].variables).toEqual({
      input: {
        id: serviceId,
        config: {
          ...makeRows()[0].config,
          capacity: -250,
          enabled: false,
          text: " RAW_TEXT_LITERAL ",
        },
      },
    });
  });
  it("edits own constructor-named wildcard keys literally and preserves every observed full config key", async () => {
    const config = JSON.parse(
      '{"capacity":4,"enabled":true,"text":"TEXT_LITERAL","locked":{"nested":[1,false,null,"NESTED_LITERAL"]},"constructor":"CTOR_LITERAL"}'
    );
    const ctx = mount(
      locale,
      "ok",
      makeRows().map((row) => ({ ...row, editableFields: ["*"], config }))
    );
    await edit(ctx, "constructor", "NEW_CTOR_LITERAL");
    expect(screen.getByLabelText("constructor")).toHaveValue("NEW_CTOR_LITERAL");
    await submit(ctx, "update");
    await waitFor(() => expect(toast.success).toHaveBeenCalledTimes(1));
    const payload = (mutations(ctx)[0].variables.input as { config: Record<string, unknown> })
      .config;
    expect(Object.keys(payload).sort()).toEqual(Object.keys(config).sort());
    expect(Object.hasOwn(payload, "constructor")).toBe(true);
    expect(payload.constructor).toBe("NEW_CTOR_LITERAL");
  });
  it("retains edited structured values as raw text instead of inventing JSON parsing", async () => {
    const ctx = mount(
      locale,
      "ok",
      makeRows().map((row) => ({ ...row, editableFields: ["*"] }))
    );
    await edit(ctx, "locked", '{"different":true}');
    await submit(ctx, "update");
    await waitFor(() => expect(toast.success).toHaveBeenCalledTimes(1));
    expect(mutations(ctx)[0].variables).toEqual({
      input: { id: serviceId, config: { ...makeRows()[0].config, locked: '{"different":true}' } },
    });
  });
  it("refuses nonfinite/empty numbers and ambiguous booleans without losing the draft or sending writes", async () => {
    const ctx = mount(locale);
    await edit(ctx, "capacity", "Infinity");
    for (const value of ["Infinity", "-Infinity", "NaN", " ", "bad"]) {
      fireEvent.change(screen.getByLabelText("capacity"), { target: { value } });
      await submit(ctx, "update");
      expect(toast.error).toHaveBeenLastCalledWith(ctx.t("invalidNumber", { field: "capacity" }));
      expect(screen.getByLabelText("capacity")).toHaveValue(value);
    }
    fireEvent.change(screen.getByLabelText("capacity"), { target: { value: "4" } });
    fireEvent.change(screen.getByLabelText("enabled"), { target: { value: "yes" } });
    await submit(ctx, "update");
    expect(toast.error).toHaveBeenLastCalledWith(ctx.t("invalidBoolean", { field: "enabled" }));
    expect(screen.getByLabelText("enabled")).toHaveValue("yes");
    expect(mutations(ctx)).toHaveLength(0);
    fireEvent.change(screen.getByLabelText("enabled"), { target: { value: " false " } });
    await submit(ctx, "update");
    await waitFor(() => expect(toast.success).toHaveBeenCalledTimes(1));
    expect(
      (mutations(ctx)[0].variables.input as { config: { enabled: boolean } }).config.enabled
    ).toBe(false);
  });
  it("keeps an unchanged review open and sends no update", async () => {
    const ctx = mount(locale);
    await edit(ctx, "capacity", "4");
    await submit(ctx, "update");
    expect(toast.message).toHaveBeenCalledWith(ctx.t("noChanges"));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(mutations(ctx)).toHaveLength(0);
  });
  it.each(["update", "reprovision"] as const)(
    "%s accepted with failed read remains accepted and exposes real retry",
    async (operation) => {
      const ctx = mount(locale, "refresh");
      if (operation === "update") await edit(ctx);
      await submit(ctx, operation);
      await waitFor(() =>
        expect(toast.warning).toHaveBeenCalledWith(ctx.t("refreshWarning"), {
          description: "RAW_MANAGED_READ_DIAGNOSTIC",
        })
      );
      await waitFor(() =>
        expect(screen.queryByRole(operation === "update" ? "dialog" : "alertdialog")).toBeNull()
      );
      expect(toast.success).toHaveBeenCalledTimes(1);
      expect(toast.error).not.toHaveBeenCalled();
      expect(screen.getByRole("alert")).toHaveTextContent("RAW_MANAGED_READ_DIAGNOSTIC");
      expect(mutations(ctx)).toHaveLength(1);
      ctx.setMode("ok");
      await userEvent.click(screen.getByRole("button", { name: ctx.t("retry") }));
      await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
      expect(mutations(ctx)).toHaveLength(1);
    }
  );
  it.each(["update", "reprovision"] as const)(
    "%s refusal/transport/fallback keeps the review and performs no automatic read",
    async (operation) => {
      const ctx = mount(locale, "refused");
      if (operation === "update") await edit(ctx);
      else {
        await ready(ctx);
        await userEvent.click(screen.getByRole("button", { name: ctx.t("reprovision") }));
      }
      const role = operation === "update" ? "dialog" : "alertdialog";
      const confirm = () =>
        userEvent.click(
          within(screen.getByRole(role)).getByRole("button", {
            name: ctx.t(operation === "update" ? "save" : "reprovision"),
          })
        );
      for (const mode of ["refused", "transport", "fallback"] as const) {
        ctx.setMode(mode);
        await confirm();
        await waitFor(() =>
          expect(toast.error).toHaveBeenLastCalledWith(
            mode === "refused"
              ? "RAW_PROVIDER_UNSUPPORTED_REFUSAL"
              : mode === "transport"
                ? "RAW_MANAGED_TRANSPORT_DIAGNOSTIC"
                : ctx.t(operation === "update" ? "updateFailed" : "reprovisionFailed")
          )
        );
        expect(screen.getByRole(role)).toBeInTheDocument();
        expect(reads(ctx)).toHaveLength(1);
      }
      expect(toast.success).not.toHaveBeenCalled();
      if (operation === "update") expect(screen.getByLabelText("capacity")).toHaveValue("8");
      expect(mutations(ctx).map((r) => r.variables)).toEqual(
        Array(3).fill(mutations(ctx)[0].variables)
      );
    }
  );
  it.each(["read", "unknown"] as const)(
    "%s inventory is unavailable, then a confirmed empty read hides the card",
    async (mode) => {
      const ctx = mount(locale, mode);
      expect(await screen.findByRole("alert")).toHaveTextContent(ctx.t("readFailed"));
      expect(screen.queryByRole("button", { name: ctx.t("reprovision") })).toBeNull();
      ctx.setMode("ok");
      ctx.setRows([]);
      await userEvent.click(screen.getByRole("button", { name: ctx.t("retry") }));
      await waitFor(() => expect(screen.queryByText(ctx.t("title"))).toBeNull());
      expect(mutations(ctx)).toHaveLength(0);
    }
  );
  it("keeps cached error reviews disabled while retry is pending and preserves draft until a successful read", async () => {
    const ctx = mount(locale);
    await edit(ctx);
    ctx.setMode("read");
    await act(async () => {
      await ctx.refresh().catch(() => {});
    });
    expect(screen.getByRole("button", { name: ctx.t("save") })).toBeDisabled();
    expect(screen.getByLabelText("capacity")).toHaveValue("8");
    ctx.setMode("pending-refresh");
    await userEvent.click(screen.getByRole("button", { name: ctx.t("retry") }));
    expect(screen.getByRole("button", { name: ctx.t("save") })).toBeDisabled();
    await act(async () => ctx.releaseRead());
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
    expect(screen.getByLabelText("capacity")).toHaveValue("8");
    expect(screen.getByRole("button", { name: ctx.t("save") })).toBeEnabled();
    expect(mutations(ctx)).toHaveLength(0);
  });
  it("invalidates config and editable-fields ABA reviews instead of merging newer config silently", async () => {
    const ctx = mount(locale);
    await edit(ctx);
    const original = ctx.rows();
    ctx.setRows(
      original.map((row) => ({ ...row, config: { ...row.config, text: "CHANGED_SOURCE" } }))
    );
    await act(async () => ctx.refresh());
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    ctx.setRows(original);
    await act(async () => ctx.refresh());
    await edit(ctx);
    expect(screen.getByLabelText("capacity")).toHaveValue("8");
    ctx.setRows(original.map((row) => ({ ...row, editableFields: ["text"] })));
    await act(async () => ctx.refresh());
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    ctx.setRows(original);
    await act(async () => ctx.refresh());
    await ready(ctx);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(mutations(ctx)).toHaveLength(0);
  });
  it("keeps the existing app.deploy gate and invalidates permission ABA reviews", async () => {
    const ctx = mount(locale);
    await edit(ctx);
    ctx.rerender({ permission: "app.update" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(screen.queryByRole("button", { name: new RegExp(`^${ctx.t("edit")}`) })).toBeNull();
    ctx.rerender({ permission: "app.deploy" });
    await ready(ctx);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(mutations(ctx)).toHaveLength(0);
  });
  it("withdraws deleted/absent services and preserves current in-flight action gating", async () => {
    const ctx = mount(locale);
    await edit(ctx);
    ctx.setRows(makeRows().map((row) => ({ ...row, status: "deleted" })));
    await act(async () => ctx.refresh());
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(screen.queryByText(ctx.t("title"))).toBeNull();
    for (const status of ["pending", "provisioning", "updating", "deprovisioning"]) {
      ctx.setRows(makeRows().map((row) => ({ ...row, status })));
      await act(async () => ctx.refresh());
      await ready(ctx);
      expect(screen.getByText(ctx.t(`status.${status}`))).toBeInTheDocument();
      expect(screen.getByRole("button", { name: new RegExp(`^${ctx.t("edit")}`) })).toBeDisabled();
      expect(screen.getByRole("button", { name: ctx.t("reprovision") })).toBeDisabled();
    }
    expect(mutations(ctx)).toHaveLength(0);
  });
  it.each(["update", "reprovision"] as const)(
    "%s stays pending through refresh and admits only one transport write",
    async (operation) => {
      const ctx = mount(locale, "pending-refresh");
      if (operation === "update") await edit(ctx);
      await submit(ctx, operation);
      await waitFor(() => expect(reads(ctx)).toHaveLength(2));
      expect(mutations(ctx)).toHaveLength(1);
      await act(async () => ctx.releaseRead());
      await waitFor(() =>
        expect(screen.queryByRole(operation === "update" ? "dialog" : "alertdialog")).toBeNull()
      );
      expect(mutations(ctx)).toHaveLength(1);
    }
  );
  it.each(["update", "reprovision"] as const)(
    "%s rejects repeated transport writes before a pending mutation resolves",
    async (operation) => {
      const ctx = mount(locale, "pending");
      if (operation === "update") await edit(ctx);
      else {
        await ready(ctx);
        await userEvent.click(screen.getByRole("button", { name: ctx.t("reprovision") }));
      }
      const dialog = screen.getByRole(operation === "update" ? "dialog" : "alertdialog");
      const button = within(dialog).getByRole("button", {
        name: ctx.t(operation === "update" ? "save" : "reprovision"),
      });
      act(() => {
        fireEvent.click(button);
        fireEvent.click(button);
      });
      await waitFor(() => expect(mutations(ctx)).toHaveLength(1));
      expect(button).toBeDisabled();
      await act(async () => ctx.releaseMutation());
      await waitFor(() => expect(toast.success).toHaveBeenCalledTimes(1));
      expect(mutations(ctx)).toHaveLength(1);
    }
  );
  it("invalidates observed service dispatch facts and reprovision source ABA", async () => {
    const ctx = mount(locale);
    await ready(ctx);
    const original = makeRows();
    for (const changed of [
      { environmentName: "ENV_B_LITERAL" },
      { kind: "KIND_B_LITERAL" },
      { variant: "VARIANT_B_LITERAL" },
      { createdAt: "2026-10-02T00:00:00Z" },
      { updatedAt: "2026-10-02T00:00:00Z" },
      { registeredAppSlug: "OTHER_OWNER_LITERAL" },
      { config: { ...original[0].config, capacity: 8 } },
      { editableFields: [] },
    ]) {
      await userEvent.click(screen.getByRole("button", { name: ctx.t("reprovision") }));
      expect(screen.getByRole("alertdialog")).toBeInTheDocument();
      ctx.setRows(original.map((row) => ({ ...row, ...changed })));
      await act(async () => ctx.refresh());
      await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
      ctx.setRows(original);
      await act(async () => ctx.refresh());
      expect(screen.queryByRole("alertdialog")).toBeNull();
    }
    expect(mutations(ctx)).toHaveLength(0);
  });
  it("an old app callback neither refreshes the new app nor closes its new review", async () => {
    const ctx = mount(locale, "pending");
    await edit(ctx);
    await submit(ctx, "update");
    await waitFor(() => expect(mutations(ctx)).toHaveLength(1));
    ctx.setRows(
      makeRows().map((row) => ({ ...row, id: guid(2), registeredAppSlug: "OTHER_APP_LITERAL" }))
    );
    ctx.rerender({ slug: "OTHER_APP_LITERAL" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await ready(ctx);
    await edit(ctx, "capacity", "12");
    const newReads = reads(ctx).length;
    await act(async () => ctx.releaseMutation());
    await waitFor(() => expect(toast.success).toHaveBeenCalledTimes(1));
    expect(reads(ctx)).toHaveLength(newReads);
    expect(screen.getByLabelText("capacity")).toHaveValue("12");
    expect(mutations(ctx)).toHaveLength(1);
  });
  it("locale changes preserve the typed review and literal field draft", async () => {
    const ctx = mount(locale);
    await edit(ctx);
    const next = locale === "es" ? "ja" : "es";
    ctx.rerender({ locale: next });
    expect(screen.getByLabelText("capacity")).toHaveValue("8");
    expect(
      screen.getByRole("button", { name: catalog(next).apps.managedServicesAdmin.save })
    ).toBeEnabled();
    expect(ctx.intlError).not.toHaveBeenCalled();
    expect(mutations(ctx)).toHaveLength(0);
  });
});
