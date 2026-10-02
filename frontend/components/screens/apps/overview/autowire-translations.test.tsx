import { readFileSync } from "node:fs";
import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { parse as parseIcu } from "@formatjs/icu-messageformat-parser";
import { act, render, renderHook, screen } from "@testing-library/react";
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
import { beforeEach, describe, expect, it, vi } from "vitest";
import { locales } from "@/i18n/config";
import { AUTOWIRE, AUTOWIRE_NOT_CONNECTED, AUTOWIRE_WIRED } from "./app-overview-banners.fixtures";
import { AutowireStatusBannerView } from "./AutowireStatusBanner";
import { OverviewNotices } from "./OverviewNotices";
import { useAutowireRetry } from "./use-autowire-retry";
const feedback = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  message: vi.fn(),
}));
vi.mock("sonner", () => ({ toast: feedback }));
const catalogs = Object.fromEntries(
  locales.map((locale) => [locale, JSON.parse(readFileSync(`messages/${locale}.json`, "utf8"))])
);
const schema = buildSchema(readFileSync("schema.graphql", "utf8"));
const fieldResolver: GraphQLFieldResolver<Record<string, unknown>, unknown> = (
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
type Mode = "wired" | "connect" | "attention" | "refused" | "fallback" | "detail" | "transport";
function harness(locale: string, mode: Mode = "wired") {
  const requests: { operationName: string; variables: Record<string, unknown> }[] = [];
  const errors = vi.fn();
  const client = new ApolloClient({
    cache: new InMemoryCache(),
    link: new HttpLink({
      uri: "https://test.invalid/gql",
      fetch: async (_url, init) => {
        const request = JSON.parse(String(init?.body));
        requests.push(request);
        const document = parse(request.query);
        expect(validate(schema, document)).toEqual([]);
        const read = request.operationName === "GetApp";
        if (!read && mode === "transport") throw new Error("RAW_TRANSPORT_DIAGNOSTIC");
        const result = await execute({
          schema,
          document,
          variableValues: request.variables,
          fieldResolver,
          rootValue: read
            ? {}
            : {
                retryAstroliftAutowire: {
                  ok: !["refused", "fallback"].includes(mode),
                  errors:
                    mode === "refused"
                      ? [{ code: "PERMISSION_DENIED", message: "RAW_SERVER_REFUSAL" }]
                      : [],
                  data: {
                    connected: mode !== "connect",
                    allOk: mode === "wired",
                    detail: mode === "detail" ? "RAW_PROVIDER_DIAGNOSTIC" : "",
                  },
                },
              },
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
        timeZone="UTC"
        onError={errors}
      >
        <ApolloProvider client={client}>{children}</ApolloProvider>
      </NextIntlClientProvider>
    );
  }
  return { Wrapper, requests, errors };
}
const translator = (locale: string) =>
  createTranslator({ locale, messages: catalogs[locale], namespace: "apps.overview.autowire" });
beforeEach(() => vi.clearAllMocks());
describe.each(locales)("auto-deploy recovery in %s", (locale) => {
  it("localizes controls and known states but keeps provider diagnostics and unknown states literal", async () => {
    const h = harness(locale);
    const t = translator(locale);
    const retry = vi.fn();
    const { rerender } = render(
      <OverviewNotices>
        <AutowireStatusBannerView
          {...AUTOWIRE}
          onRetry={retry}
          autowire={{
            ...AUTOWIRE.autowire!,
            ciWorkflow: "missing",
            webhook: "rate_limited",
            secrets: "phantom",
            detail: "RAW_PROVIDER_DIAGNOSTIC",
          }}
        />
      </OverviewNotices>,
      { wrapper: h.Wrapper }
    );
    expect(screen.getByRole("region")).toHaveAccessibleName(
      catalogs[locale].apps.overview.noticesLabel
    );
    for (const text of [
      t("incompleteTitle"),
      t("statuses.missing"),
      t("statuses.phantom"),
      "rate_limited",
      "RAW_PROVIDER_DIAGNOSTIC",
    ])
      expect(screen.getByText(text)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: t("retry") }));
    expect(retry).toHaveBeenCalledTimes(1);
    rerender(<AutowireStatusBannerView {...AUTOWIRE} retrying />);
    expect(screen.getByRole("button", { name: t("retry") })).toBeDisabled();
    rerender(<AutowireStatusBannerView {...AUTOWIRE} autowire={AUTOWIRE_NOT_CONNECTED} />);
    expect(screen.getByRole("link", { name: t("connect") })).toHaveAttribute(
      "href",
      "/settings/source-providers"
    );
    rerender(<AutowireStatusBannerView {...AUTOWIRE} autowire={AUTOWIRE_WIRED} />);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    rerender(<AutowireStatusBannerView {...AUTOWIRE} sourceKind="container" />);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(h.errors).not.toHaveBeenCalled();
  });
  it.each(["wired", "connect", "attention", "fallback", "refused", "detail", "transport"] as const)(
    "preserves real SDL/HTTP retry and refresh for %s",
    async (mode) => {
      const h = harness(locale, mode);
      const t = translator(locale);
      const { result } = renderHook(() => useAutowireRetry("literal-app"), { wrapper: h.Wrapper });
      await act(() => result.current.onRetry());
      expect(h.requests[0].variables).toEqual({ input: { appSlug: "literal-app" } });
      const expected = {
        wired: [feedback.success, t("feedback.wired")],
        connect: [feedback.message, t("feedback.connect")],
        attention: [feedback.warning, t("feedback.attention")],
        fallback: [feedback.error, t("feedback.failed")],
        refused: [feedback.error, "RAW_SERVER_REFUSAL"],
        detail: [feedback.warning, "RAW_PROVIDER_DIAGNOSTIC"],
        transport: [feedback.error, "RAW_TRANSPORT_DIAGNOSTIC"],
      }[mode];
      expect(expected[0]).toHaveBeenCalledWith(expected[1]);
      if (mode !== "transport")
        expect(
          h.requests.some(
            (r) =>
              r.operationName === "GetApp" &&
              r.variables.slug === "literal-app" &&
              r.variables.includeDrift === true
          )
        ).toBe(true);
      expect(h.errors).not.toHaveBeenCalled();
    }
  );
  it("has complete valid ICU messages", () => {
    function flatten(value: Record<string, unknown>, prefix = ""): Record<string, string> {
      return Object.fromEntries(
        Object.entries(value).flatMap(([key, v]) =>
          typeof v === "string"
            ? [[prefix + key, v]]
            : Object.entries(flatten(v as Record<string, unknown>, prefix + key + "."))
        )
      );
    }
    const source = flatten(catalogs.en.apps.overview.autowire);
    const messages = flatten(catalogs[locale].apps.overview.autowire);
    expect(Object.keys(messages)).toEqual(Object.keys(source));
    for (const [key, value] of Object.entries(messages)) {
      expect(value.trim()).not.toBe("");
      expect(parseIcu(value)).toBeDefined();
      if (locale !== "en") expect(value).not.toBe(source[key]);
    }
  });
});
