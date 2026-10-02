import { readFileSync } from "node:fs";
import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { parse as parseIcu } from "@formatjs/icu-messageformat-parser";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  buildSchema,
  execute,
  parse,
  validate,
  getNamedType,
  isNonNullType,
  isListType,
  isObjectType,
  isEnumType,
  type GraphQLFieldResolver,
} from "graphql";
import { createTranslator, NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { locales } from "@/i18n/config";
import { TooltipProvider } from "@/components/ui/tooltip";
import { ActiveOrgProvider } from "@/graphql/identity/identity.hooks";
import { LIST_ORGANIZATIONS } from "@/graphql/identity/identity.queries";
import { AgentModelAccessCard } from "@/app/(app)/agents/[agentSlug]/components/agent-model-access-card";
import { ENV_SPEC } from "./agent-detail-shell.fixtures";
const notifications = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));
vi.mock("sonner", () => ({ toast: notifications }));
const catalogs = Object.fromEntries(
  locales.map((locale) => [locale, JSON.parse(readFileSync(`messages/${locale}.json`, "utf8"))])
);
const tFor = (locale: string) =>
  createTranslator({ locale, messages: catalogs[locale], namespace: "agentModelAccess" });
const schema = buildSchema(readFileSync("schema.graphql", "utf8"));
const orgId = "71366f2c-4458-4c97-bf4a-476f21a25a82",
  specId = "71366f2c-4458-4c97-bf4a-476f21a25a83",
  slug = "literal-env/空";
const fieldResolver: GraphQLFieldResolver<Record<string, unknown>, unknown> = (
  object,
  args,
  _context,
  info
) => {
  if (object && info.fieldName in object) {
    const value = object[info.fieldName];
    return typeof value === "function" ? value(args) : value;
  }
  const type = getNamedType(info.returnType),
    wrapped = isNonNullType(info.returnType) ? info.returnType.ofType : info.returnType;
  if (isListType(wrapped)) return [];
  if (isObjectType(type)) return {};
  if (isEnumType(type)) return type.getValues()[0].name;
  return type.name === "Boolean" ? false : ["Int", "Float"].includes(type.name) ? 0 : "";
};
type Mode =
  | "success"
  | "refused"
  | "missingEnvelope"
  | "nullData"
  | "nullDataPersisted"
  | "missingID"
  | "wrongSlug"
  | "wrongValue"
  | "wrongID"
  | "wireFail";
function harness(
  locale: string,
  mode: Mode = "success",
  read: "normal" | "empty" | "refused" | "noOrg" = "normal"
) {
  let readMode = read,
    delivered = 0,
    release: (() => void) | undefined,
    gate: Promise<void> | undefined;
  const requests: Array<{ operationName: string; variables: Record<string, unknown> }> = [],
    errors = vi.fn();
  const specs = [
    { ...ENV_SPEC, id: specId, slug, managedModel: false, vncEnabled: false },
    {
      ...ENV_SPEC,
      id: "71366f2c-4458-4c97-bf4a-476f21a25a84",
      slug: "literal-other",
      managedModel: false,
      vncEnabled: false,
    },
  ];
  const client = new ApolloClient({
    cache: new InMemoryCache(),
    link: new HttpLink({
      uri: "https://test.invalid/graphql",
      fetch: async (_url, init) => {
        const request = JSON.parse(String(init?.body));
        requests.push(request);
        const document = parse(request.query);
        expect(validate(schema, document)).toEqual([]);
        const mutation = request.operationName === "UpdateAgentEnvironmentSpec";
        if (mutation && mode === "wireFail") {
          delivered++;
          throw new Error("RAW_UPDATE_WIRE_DIAGNOSTIC");
        }
        if (!mutation && readMode === "refused" && request.operationName !== "ListOrganizations")
          throw new Error("RAW_ENV_SPEC_DIAGNOSTIC");
        if (mutation && mode === "missingEnvelope") {
          delivered++;
          return new Response(JSON.stringify({ data: {} }), {
            headers: { "Content-Type": "application/json" },
          });
        }
        const result = await execute({
          schema,
          document,
          variableValues: request.variables,
          fieldResolver,
          rootValue: {
            astroliftOrganizations:
              readMode === "noOrg"
                ? []
                : [{ id: orgId, name: "RAW_ORG_NAME", slug: "literal-org" }],
            agentEnvironmentSpecs: readMode === "empty" ? [] : specs,
            updateAgentEnvironmentSpec: (args: {
              slug: string;
              input: { managedModel?: boolean; vncEnabled?: boolean };
            }) => {
              const selected = specs.find((spec) => spec.slug === args.slug)!;
              const values = Object.fromEntries(
                Object.entries(args.input).filter(
                  ([key, value]) =>
                    ["managedModel", "vncEnabled"].includes(key) && typeof value === "boolean"
                )
              );
              if (mode === "success" || mode === "nullDataPersisted")
                Object.assign(selected, values);
              if (mode === "refused")
                return {
                  ok: false,
                  errors: [{ code: "REFUSED", message: "RAW_UPDATE_DIAGNOSTIC", field: null }],
                  data: null,
                };
              const data = { ...selected, ...values };
              if (mode === "missingID") data.id = "";
              if (mode === "wrongSlug") data.slug = "RAW_FOREIGN_SLUG";
              if (mode === "wrongID") data.id = "71366f2c-4458-4c97-bf4a-476f21a25a89";
              if (mode === "wrongValue") {
                if (args.input.managedModel !== undefined)
                  data.managedModel = !args.input.managedModel;
                if (args.input.vncEnabled !== undefined) data.vncEnabled = !args.input.vncEnabled;
              }
              return {
                ok: true,
                errors: [],
                data: mode === "nullData" || mode === "nullDataPersisted" ? null : data,
              };
            },
          },
        });
        expect(
          "errors" in result ? result.errors?.map((error) => error.message) : undefined
        ).toBeUndefined();
        if (mutation && gate) await gate;
        if (mutation) delivered++;
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
        timeZone="UTC"
        onError={errors}
      >
        <ApolloProvider client={client}>
          <ActiveOrgProvider>
            <TooltipProvider>{children}</TooltipProvider>
          </ActiveOrgProvider>
        </ApolloProvider>
      </NextIntlClientProvider>
    );
  }
  return {
    Wrapper,
    client,
    requests,
    errors,
    replaceSpec: () => {
      specs[0] = {
        ...specs[0],
        id: "71366f2c-4458-4c97-bf4a-476f21a25a88",
        managedModel: false,
        vncEnabled: false,
      };
    },
    refreshSpecs: () => client.refetchQueries({ include: ["ListAgentEnvironmentSpecs"] }),
    recover: () => {
      readMode = "normal";
    },
    hold: () => {
      let resolveHold!: () => void;
      gate = new Promise<void>((resolve) => {
        resolveHold = resolve;
      });
      release = resolveHold;
      return resolveHold;
    },
    release: () => release?.(),
    delivered: () => delivered,
  };
}
const controls = [
  {
    field: "managedModel",
    enable: "modelEnable",
    disable: "modelDisable",
    savedOn: "modelSavedOn",
    savedOff: "modelSavedOff",
    failed: "modelUpdateFailed",
  },
  {
    field: "vncEnabled",
    enable: "vncEnable",
    disable: "vncDisable",
    savedOn: "vncSavedOn",
    savedOff: "vncSavedOff",
    failed: "vncUpdateFailed",
  },
] as const;
beforeEach(() => {
  notifications.error.mockClear();
  notifications.success.mockClear();
});
describe.each(locales)("connected model and live-session settings in %s", (locale) => {
  it("resolves the active org and spec through actual HTTP/current SDL and shows localized headings", async () => {
    const h = harness(locale),
      t = tFor(locale);
    render(<AgentModelAccessCard agentSlug={slug} />, { wrapper: h.Wrapper });
    expect(await screen.findByRole("switch", { name: t("modelEnable") })).toHaveAttribute(
      "aria-checked",
      "false"
    );
    expect(screen.getByRole("switch", { name: t("vncEnable") })).toHaveAttribute(
      "aria-checked",
      "false"
    );
    for (const key of ["title", "description", "liveTitle", "liveDescription"] as const)
      expect(screen.getByText(t(key))).toBeInTheDocument();
    expect(
      h.requests.find((r) => r.operationName === "ListAgentEnvironmentSpecs")?.variables
    ).toEqual({ orgId });
    expect(h.errors).not.toHaveBeenCalled();
    h.client.stop();
  });
  it("shows a missing spec only after an actual empty read and offers no update control", async () => {
    const h = harness(locale, "success", "empty"),
      t = tFor(locale);
    render(<AgentModelAccessCard agentSlug={slug} />, { wrapper: h.Wrapper });
    expect(await screen.findAllByText(t("noSpec"))).toHaveLength(2);
    expect(screen.queryByRole("switch")).not.toBeInTheDocument();
    expect(notifications.success).not.toHaveBeenCalled();
    h.client.stop();
  });
  it("preserves refused read diagnostics, avoids false missing-spec guidance, and retries metadata in the same org", async () => {
    const h = harness(locale, "success", "refused"),
      t = tFor(locale);
    render(<AgentModelAccessCard agentSlug={slug} />, { wrapper: h.Wrapper });
    expect(await screen.findByRole("alert")).toHaveTextContent(t("readFailed"));
    expect(screen.getByText("RAW_ENV_SPEC_DIAGNOSTIC")).toBeInTheDocument();
    expect(screen.queryByText(t("noSpec"))).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: t("retry") }));
    await waitFor(() =>
      expect(
        h.requests.filter((r) => r.operationName === "ListAgentEnvironmentSpecs")
      ).toHaveLength(2)
    );
    expect(screen.getByText("RAW_ENV_SPEC_DIAGNOSTIC")).toBeInTheDocument();
    expect(screen.queryByText(t("noSpec"))).not.toBeInTheDocument();
    h.recover();
    await userEvent.click(screen.getByRole("button", { name: t("retry") }));
    expect(await screen.findByRole("switch", { name: t("modelEnable") })).toBeEnabled();
    expect(
      h.requests
        .filter((r) => r.operationName === "ListAgentEnvironmentSpecs")
        .map((r) => r.variables)
    ).toEqual([{ orgId }, { orgId }, { orgId }]);
    expect(h.errors).not.toHaveBeenCalled();
    h.client.stop();
  });
  it("does not query specs or offer updates without a resolved org", async () => {
    const h = harness(locale, "success", "noOrg");
    render(<AgentModelAccessCard agentSlug={slug} />, { wrapper: h.Wrapper });
    await waitFor(() =>
      expect(h.client.readQuery({ query: LIST_ORGANIZATIONS })).toEqual({
        astroliftOrganizations: [],
      })
    );
    expect(h.requests).toHaveLength(1);
    expect(h.requests[0].operationName).toBe("ListOrganizations");
    expect(screen.queryByRole("switch")).not.toBeInTheDocument();
    h.client.stop();
  });
  for (const control of controls) {
    it(`acknowledges ${control.field} only for the exact spec and requested boolean, including disabling it`, async () => {
      const h = harness(locale),
        t = tFor(locale);
      render(<AgentModelAccessCard agentSlug={slug} />, { wrapper: h.Wrapper });
      await userEvent.click(await screen.findByRole("switch", { name: t(control.enable) }));
      await waitFor(() => expect(notifications.success).toHaveBeenCalledWith(t(control.savedOn)));
      expect(
        h.requests.find((r) => r.operationName === "UpdateAgentEnvironmentSpec")?.variables
      ).toEqual({ slug, input: { [control.field]: true } });
      const enabled = await screen.findByRole("switch", { name: t(control.disable) });
      await waitFor(() => expect(enabled).toBeEnabled());
      await userEvent.click(enabled);
      await waitFor(() =>
        expect(notifications.success).toHaveBeenLastCalledWith(t(control.savedOff))
      );
      expect(
        h.requests.filter((r) => r.operationName === "UpdateAgentEnvironmentSpec")[1].variables
      ).toEqual({ slug, input: { [control.field]: false } });
      expect(notifications.error).not.toHaveBeenCalled();
      expect(h.errors).not.toHaveBeenCalled();
      h.client.stop();
    });
    for (const mode of [
      "refused",
      "missingEnvelope",
      "nullData",
      "missingID",
      "wrongSlug",
      "wrongValue",
      "wrongID",
      "wireFail",
    ] as const) {
      it(`does not claim ${control.field} succeeded after ${mode}`, async () => {
        const h = harness(locale, mode),
          t = tFor(locale);
        render(<AgentModelAccessCard agentSlug={slug} />, { wrapper: h.Wrapper });
        await userEvent.click(await screen.findByRole("switch", { name: t(control.enable) }));
        await waitFor(() => expect(notifications.error).toHaveBeenCalled());
        expect(notifications.success).not.toHaveBeenCalled();
        const diagnostic =
          mode === "refused"
            ? "RAW_UPDATE_DIAGNOSTIC"
            : mode === "wireFail"
              ? "RAW_UPDATE_WIRE_DIAGNOSTIC"
              : mode === "missingEnvelope"
                ? t("unknownError")
                : null;
        expect(notifications.error).toHaveBeenCalledWith(
          diagnostic ? t(control.failed, { reason: diagnostic }) : t("updateUnconfirmed")
        );
        const unchanged = await screen.findByRole("switch", { name: t(control.enable) });
        await waitFor(() => expect(unchanged).toBeEnabled());
        expect(unchanged).toHaveAttribute("aria-checked", "false");
        expect(
          h.requests.filter((r) => r.operationName === "UpdateAgentEnvironmentSpec")
        ).toHaveLength(1);
        expect(h.errors).not.toHaveBeenCalled();
        h.client.stop();
      });
    }
  }
  it("has all 25 nonempty valid ICU messages and literal reason placeholders", () => {
    const localized = catalogs[locale].agentModelAccess;
    expect(Object.keys(localized).sort()).toEqual(Object.keys(catalogs.en.agentModelAccess).sort());
    expect(Object.keys(localized)).toHaveLength(25);
    for (const text of Object.values(localized) as string[]) {
      expect(text.trim()).not.toBe("");
      expect(() => parseIcu(text)).not.toThrow();
    }
    for (const key of ["modelUpdateFailed", "vncUpdateFailed"] as const)
      expect(tFor(locale)(key, { reason: "RAW_reason/{GUID}" })).toContain("RAW_reason/{GUID}");
  });
});
for (const control of controls) {
  it(`keeps ${control.field} disabled during pending write and emits no duplicate dispatch`, async () => {
    const h = harness("fr"),
      t = tFor("fr");
    h.hold();
    render(<AgentModelAccessCard agentSlug={slug} />, { wrapper: h.Wrapper });
    await userEvent.click(await screen.findByRole("switch", { name: t(control.enable) }));
    const pending = screen.getByRole("switch", { name: t(control.disable) });
    expect(pending).toBeDisabled();
    await userEvent.click(pending);
    expect(h.requests.filter((r) => r.operationName === "UpdateAgentEnvironmentSpec")).toHaveLength(
      1
    );
    h.release();
    await waitFor(() => expect(notifications.success).toHaveBeenCalledWith(t(control.savedOn)));
    h.client.stop();
  });
  it(`suppresses delayed ${control.field} feedback/state after the selected spec changes`, async () => {
    const h = harness("ja"),
      t = tFor("ja");
    h.hold();
    const view = render(<AgentModelAccessCard agentSlug={slug} />, { wrapper: h.Wrapper });
    await userEvent.click(await screen.findByRole("switch", { name: t(control.enable) }));
    view.rerender(<AgentModelAccessCard agentSlug="literal-other" />);
    const successor = screen.getByRole("switch", { name: t(control.enable) });
    expect(successor).toHaveAttribute("aria-checked", "false");
    expect(successor).toBeEnabled();
    h.release();
    await waitFor(() => expect(h.delivered()).toBe(1));
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(notifications.success).not.toHaveBeenCalled();
    expect(notifications.error).not.toHaveBeenCalled();
    expect(screen.getByRole("switch", { name: t(control.enable) })).toHaveAttribute(
      "aria-checked",
      "false"
    );
    h.client.stop();
  });
  it(`suppresses delayed ${control.field} notifications after unmount`, async () => {
    const h = harness("fr"),
      t = tFor("fr");
    h.hold();
    const view = render(<AgentModelAccessCard agentSlug={slug} />, { wrapper: h.Wrapper });
    await userEvent.click(await screen.findByRole("switch", { name: t(control.enable) }));
    view.unmount();
    h.release();
    await waitFor(() => expect(h.delivered()).toBe(1));
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(notifications.success).not.toHaveBeenCalled();
    expect(notifications.error).not.toHaveBeenCalled();
    h.client.stop();
  });
}

for (const control of controls) {
  it(`suppresses delayed ${control.field} receipt after a same-slug spec GUID replacement`, async () => {
    const h = harness("ja"),
      t = tFor("ja");
    h.hold();
    render(<AgentModelAccessCard agentSlug={slug} />, { wrapper: h.Wrapper });
    await userEvent.click(await screen.findByRole("switch", { name: t(control.enable) }));
    h.replaceSpec();
    await act(async () => {
      await h.refreshSpecs();
    });
    const replacement = screen.getByRole("switch", { name: t(control.enable) });
    expect(replacement).toBeEnabled();
    expect(replacement).toHaveAttribute("aria-checked", "false");
    h.release();
    await waitFor(() => expect(h.delivered()).toBe(1));
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(notifications.success).not.toHaveBeenCalled();
    expect(notifications.error).not.toHaveBeenCalled();
    expect(screen.getByRole("switch", { name: t(control.enable) })).toHaveAttribute(
      "aria-checked",
      "false"
    );
    h.client.stop();
  });
}

for (const control of controls) {
  it(`cannot clear the successor ${control.field} pending state when the old spec receipt arrives`, async () => {
    const h = harness("fr"),
      t = tFor("fr"),
      releaseOld = h.hold();
    const view = render(<AgentModelAccessCard agentSlug={slug} />, { wrapper: h.Wrapper });
    await userEvent.click(await screen.findByRole("switch", { name: t(control.enable) }));
    view.rerender(<AgentModelAccessCard agentSlug="literal-other" />);
    const releaseNew = h.hold();
    await userEvent.click(screen.getByRole("switch", { name: t(control.enable) }));
    expect(
      h.requests
        .filter((request) => request.operationName === "UpdateAgentEnvironmentSpec")
        .map((request) => request.variables)
    ).toEqual([
      { slug, input: { [control.field]: true } },
      { slug: "literal-other", input: { [control.field]: true } },
    ]);
    releaseOld();
    await waitFor(() => expect(h.delivered()).toBe(1));
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(screen.getByRole("switch", { name: t(control.disable) })).toBeDisabled();
    expect(notifications.success).not.toHaveBeenCalled();
    expect(notifications.error).not.toHaveBeenCalled();
    releaseNew();
    await waitFor(() =>
      expect(notifications.success).toHaveBeenCalledExactlyOnceWith(t(control.savedOn))
    );
    expect(screen.getByRole("switch", { name: t(control.disable) })).toBeEnabled();
    h.client.stop();
  });
}

for (const control of controls) {
  it(`preserves independently refreshed ${control.field} metadata when the write receipt is unconfirmed`, async () => {
    const h = harness("fr", "nullDataPersisted"),
      t = tFor("fr");
    h.hold();
    render(<AgentModelAccessCard agentSlug={slug} />, { wrapper: h.Wrapper });
    await userEvent.click(await screen.findByRole("switch", { name: t(control.enable) }));
    await act(async () => {
      await h.refreshSpecs();
    });
    expect(screen.getByRole("switch", { name: t(control.disable) })).toHaveAttribute(
      "aria-checked",
      "true"
    );
    h.release();
    await waitFor(() => expect(notifications.error).toHaveBeenCalledWith(t("updateUnconfirmed")));
    expect(notifications.success).not.toHaveBeenCalled();
    expect(screen.getByRole("switch", { name: t(control.disable) })).toHaveAttribute(
      "aria-checked",
      "true"
    );
    h.client.stop();
  });
}
