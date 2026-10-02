import { readFileSync } from "node:fs";
import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { parse as parseMessage } from "@formatjs/icu-messageformat-parser";
import { act, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
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
import { useState, type ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { locales } from "@/i18n/config";
import { REGISTER } from "./fixtures";
import { RegisterClusterPage } from "./RegisterClusterPage";
import { useRegisterCluster, type RegisterClusterInput } from "./use-register-cluster";

const state = vi.hoisted(() => ({ push: vi.fn(), success: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: state.push }) }));
vi.mock("sonner", () => ({ toast: { success: state.success } }));
const catalogs = Object.fromEntries(
  locales.map((locale) => [locale, JSON.parse(readFileSync(`messages/${locale}.json`, "utf8"))])
);
const schema = buildSchema(readFileSync("../backend/schema.graphql", "utf8"));
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
type Mode = "accepted" | "refused" | "fallback" | "transport" | "empty-plugins" | "regions-failed";
function harness(locale: string, initial: Mode = "accepted") {
  let mode = initial;
  let updateLocale: (value: string) => void = () => undefined;
  const requests: { operationName: string; variables: Record<string, unknown> }[] = [];
  const client = new ApolloClient({
    cache: new InMemoryCache(),
    link: new HttpLink({
      uri: "https://registration.test.invalid/graphql/",
      fetch: async (_url, options) => {
        const request = JSON.parse(String(options?.body));
        requests.push({ operationName: request.operationName, variables: request.variables });
        const document = parse(request.query);
        expect(validate(schema, document)).toEqual([]);
        if (
          (request.operationName === "RegisterTenantCluster" && mode === "transport") ||
          (request.operationName === "ProviderRegions" && mode === "regions-failed")
        )
          throw new Error("RAW_TRANSPORT_DIAGNOSTIC");
        const input = request.variables.input ?? {};
        const rootValue = {
          astroliftProviderPlugins: mode === "empty-plugins" ? [] : REGISTER.plugins,
          astroliftProviderRegions: REGISTER.regions,
          astroliftClusters: [],
          registerTenantCluster: {
            ok: !["refused", "fallback"].includes(mode),
            errors:
              mode === "refused"
                ? [
                    { code: "CONFLICT", field: "slug", message: "RAW_SERVER_SLUG_REFUSAL" },
                    { code: "INVALID", field: "slug", message: "RAW_SECOND_SLUG_REFUSAL" },
                    { code: "INVALID", field: "futureField", message: "RAW_UNKNOWN_FIELD_REFUSAL" },
                  ]
                : [],
            data: {
              ...input,
              region: input.region ?? "",
              id: "c0ffee00-0000-4000-8000-000000000001",
              slug: "SERVER_SLUG_LITERAL",
            },
          },
        };
        const result = await execute({
          schema,
          document,
          variableValues: request.variables,
          rootValue,
          fieldResolver,
        });
        expect("errors" in result ? result.errors : undefined).toBeUndefined();
        return Response.json(result);
      },
    }),
  });
  function Wrapper({ children }: { children: ReactNode }) {
    const [currentLocale, setLocale] = useState(locale);
    updateLocale = setLocale;
    return (
      <NextIntlClientProvider
        locale={currentLocale}
        messages={catalogs[currentLocale]}
        now={new Date("2026-10-02T12:00:00Z")}
        timeZone="UTC"
      >
        <ApolloProvider client={client}>{children}</ApolloProvider>
      </NextIntlClientProvider>
    );
  }
  function Connected() {
    return <RegisterClusterPage {...useRegisterCluster()} cancelHref="/clusters" />;
  }
  return {
    Wrapper,
    Connected,
    requests,
    setMode: (value: Mode) => {
      mode = value;
    },
    locale: (value: string) => updateLocale(value),
  };
}
const tFor = (locale: string) =>
  createTranslator({ locale, messages: catalogs[locale], namespace: "clusters.registration" });
const input: RegisterClusterInput = {
  name: "NAME_LITERAL",
  slug: "SLUG_LITERAL",
  providerPluginSlug: "eks",
  authMethod: "exec_plugin",
  region: "REGION_LITERAL",
  endpoint: "https://kube.example.com",
  ingressClass: "INGRESS_LITERAL",
  authConfig: { cluster_name: "CLUSTER_LITERAL", region: "REGION_LITERAL" },
};
beforeEach(() => vi.clearAllMocks());
async function enterConnection(t: ReturnType<typeof tFor>) {
  await userEvent.type(screen.getByLabelText(t("name")), "literal-cluster");
  await userEvent.click(screen.getByRole("button", { name: t("continue") }));
  return screen.findByLabelText(t("authConfig"));
}

describe.each(locales)("cluster registration in %s", (locale) => {
  it("localizes connected controls and client validation, keeping provider data literal", async () => {
    const h = harness(locale);
    const t = tFor(locale);
    render(<h.Connected />, { wrapper: h.Wrapper });
    await waitFor(() => expect(screen.getByRole("button", { name: t("continue") })).toBeEnabled());
    expect(screen.getByRole("heading", { name: t("title") })).toBeInTheDocument();
    expect(screen.getByRole("list", { name: t("steps") })).toHaveTextContent(t("cluster"));
    expect(screen.getByText("AWS EKS")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: t("continue") }));
    expect(screen.getByText(t("nameRequired"))).toBeInTheDocument();
    expect(screen.getByLabelText(t("slug"))).toHaveAttribute("aria-invalid", "true");
    await userEvent.type(screen.getByLabelText(t("name")), "Name Literal");
    fireEvent.change(screen.getByLabelText(t("slug")), { target: { value: "UPPER_invalid" } });
    await userEvent.click(screen.getByRole("button", { name: t("continue") }));
    expect(screen.getByText(t("slugInvalid"))).toBeInTheDocument();
    expect(h.requests.some((r) => r.operationName === "RegisterTenantCluster")).toBe(false);
  });

  it("keeps local JSON errors generic and never echoes credential text", async () => {
    const h = harness(locale);
    const t = tFor(locale);
    render(<h.Connected />, { wrapper: h.Wrapper });
    await waitFor(() => expect(screen.getByRole("button", { name: t("continue") })).toBeEnabled());
    const auth = await enterConnection(t);
    fireEvent.change(screen.getByLabelText(t("endpoint")), { target: { value: "not-a-url" } });
    await userEvent.click(screen.getByRole("button", { name: t("title") }));
    expect(screen.getByText(t("endpointInvalid"))).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(t("endpoint")), { target: { value: "" } });
    fireEvent.change(auth, { target: { value: "SECRET_CREDENTIAL_MARKER" } });
    await userEvent.click(screen.getByRole("button", { name: t("title") }));
    expect(screen.getByText(t("authConfigInvalid"))).toBeInTheDocument();
    for (const alert of screen.getAllByRole("alert"))
      expect(alert).not.toHaveTextContent("SECRET_CREDENTIAL_MARKER");
    expect(h.requests.some((r) => r.operationName === "RegisterTenantCluster")).toBe(false);
    expect(state.success).not.toHaveBeenCalled();
  });

  it("submits free-text regions and converted YAML unchanged through checked SDL and HTTP", async () => {
    const h = harness(locale, "regions-failed");
    const t = tFor(locale);
    render(<h.Connected />, { wrapper: h.Wrapper });
    await waitFor(() => expect(screen.getByRole("button", { name: t("continue") })).toBeEnabled());
    fireEvent.change(screen.getByLabelText(t("region")), {
      target: { value: "future-region-literal" },
    });
    await enterConnection(t);
    const raw = 'apiVersion: v1\nkind: Config\nuser: "SECRET_LITERAL"';
    fireEvent.change(screen.getByLabelText(t("pasteKubeconfig")), { target: { value: raw } });
    await userEvent.click(screen.getByRole("button", { name: t("convertAuthConfig") }));
    expect(screen.getByLabelText(t("authConfig"))).toHaveValue(
      JSON.stringify({ kubeconfig: raw }, null, 2)
    );
    await userEvent.click(screen.getByRole("button", { name: t("title") }));
    await waitFor(() => expect(state.push).toHaveBeenCalledWith("/clusters/SERVER_SLUG_LITERAL"));
    expect(state.success).toHaveBeenCalledWith(t("registered", { slug: "literal-cluster" }));
    expect(h.requests.find((r) => r.operationName === "RegisterTenantCluster")?.variables).toEqual({
      input: {
        name: "literal-cluster",
        slug: "literal-cluster",
        providerPluginSlug: "eks",
        authMethod: "kubeconfig",
        region: "future-region-literal",
        endpoint: null,
        ingressClass: "nginx",
        authConfig: { kubeconfig: raw },
      },
    });
    expect(h.requests.some((r) => r.operationName === "ListClusters")).toBe(true);
  });

  it("preserves edited credentials across auth-method and locale changes", async () => {
    const h = harness(locale);
    const t = tFor(locale);
    render(<h.Connected />, { wrapper: h.Wrapper });
    await waitFor(() => expect(screen.getByRole("button", { name: t("continue") })).toBeEnabled());
    await enterConnection(t);
    const draft = JSON.stringify({ token: "SECRET_LITERAL", unknown_key: "OPAQUE_LITERAL" });
    fireEvent.change(screen.getByLabelText(t("authConfig")), { target: { value: draft } });
    await userEvent.click(screen.getByLabelText(t("authMethod")));
    await userEvent.click(screen.getByRole("option", { name: "service_account_token" }));
    expect(screen.getByLabelText(t("authConfig"))).toHaveValue(draft);
    const nextLocale = locale === "ja" ? "de" : "ja";
    await act(() => h.locale(nextLocale));
    const next = tFor(nextLocale);
    expect(screen.getByLabelText(next("authConfig"))).toHaveValue(draft);
    expect(screen.getByLabelText(next("authMethod"))).toHaveTextContent("service_account_token");
    expect(screen.getByText(next("hintServiceAccount"))).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: next("title") }));
    await waitFor(() =>
      expect(state.success).toHaveBeenCalledWith(next("registered", { slug: "literal-cluster" }))
    );
    expect(
      h.requests.find((r) => r.operationName === "RegisterTenantCluster")?.variables
    ).toMatchObject({ input: { authConfig: JSON.parse(draft) } });
  });

  it("seeds translated examples with the exact provider auth keys until the operator edits", async () => {
    const h = harness(locale);
    const t = tFor(locale);
    render(<h.Connected />, { wrapper: h.Wrapper });
    await waitFor(() => expect(screen.getByRole("button", { name: t("continue") })).toBeEnabled());
    await enterConnection(t);
    const auth = screen.getByLabelText(t("authConfig")) as HTMLTextAreaElement;
    expect(JSON.parse(auth.value)).toEqual({ kubeconfig: `<${t("exampleKubeconfig")}>` });
    await userEvent.click(screen.getByLabelText(t("authMethod")));
    await userEvent.click(screen.getByRole("option", { name: "exec_plugin" }));
    expect(JSON.parse(auth.value)).toEqual({
      region: `<${t("exampleRegion")}>`,
      cluster_name: `<${t("exampleCluster")}>`,
    });
    expect(screen.getByText(t("hintExecPlugin"))).toBeInTheDocument();
    await userEvent.click(screen.getByLabelText(t("authMethod")));
    await userEvent.click(screen.getByRole("option", { name: "service_account_token" }));
    expect(JSON.parse(auth.value)).toEqual({
      token: `<${t("exampleToken")}>`,
      ca_cert: `<${t("exampleCa")}>`,
    });
    expect(screen.getByText(t("hintServiceAccount"))).toBeInTheDocument();
    expect(h.requests.some((r) => r.operationName === "RegisterTenantCluster")).toBe(false);
  });

  it("keeps exact server field/form diagnostics and supports retry without losing drafts", async () => {
    const h = harness(locale, "refused");
    const t = tFor(locale);
    render(<h.Connected />, { wrapper: h.Wrapper });
    await waitFor(() => expect(screen.getByRole("button", { name: t("continue") })).toBeEnabled());
    await enterConnection(t);
    await userEvent.click(screen.getByRole("button", { name: t("title") }));
    expect(await screen.findByText("RAW_SERVER_SLUG_REFUSAL")).toBeInTheDocument();
    expect(
      screen.getByText("RAW_SECOND_SLUG_REFUSAL RAW_UNKNOWN_FIELD_REFUSAL")
    ).toBeInTheDocument();
    expect(screen.getByLabelText(t("name"))).toHaveValue("literal-cluster");
    expect(state.push).not.toHaveBeenCalled();
    h.setMode("accepted");
    await userEvent.click(screen.getByRole("button", { name: t("continue") }));
    await userEvent.click(screen.getByRole("button", { name: t("title") }));
    await waitFor(() => expect(state.push).toHaveBeenCalledTimes(1));
    expect(h.requests.filter((r) => r.operationName === "RegisterTenantCluster")).toHaveLength(2);
  });

  it("translates empty refusal fallback and preserves transport errors", async () => {
    const h = harness(locale, "fallback");
    const t = tFor(locale);
    const { result } = renderHook(useRegisterCluster, { wrapper: h.Wrapper });
    let refused: unknown;
    await act(async () => {
      refused = await result.current.onRegister(input);
    });
    expect(refused).toEqual({ ok: false, fieldErrors: {}, formError: t("failed") });
    h.setMode("transport");
    await act(async () => {
      await expect(result.current.onRegister(input)).rejects.toThrow("RAW_TRANSPORT_DIAGNOSTIC");
    });
    expect(state.success).not.toHaveBeenCalled();
    expect(state.push).not.toHaveBeenCalled();
  });

  it("disables registration when no providers are available and hides native regions", () => {
    const t = tFor(locale);
    const h = harness(locale);
    const { rerender } = render(<RegisterClusterPage {...REGISTER} plugins={[]} pluginSlug="" />, {
      wrapper: h.Wrapper,
    });
    expect(screen.getByRole("button", { name: t("continue") })).toBeDisabled();
    expect(screen.getByText(t("noPlugins"))).toBeInTheDocument();
    rerender(<RegisterClusterPage {...REGISTER} pluginSlug="k8s_native" />);
    expect(screen.queryByLabelText(t("region"))).not.toBeInTheDocument();
  });

  it("parses every message and keeps identical catalog keys and technical auth identifiers", () => {
    const current = catalogs[locale].clusters.registration;
    expect(Object.keys(current)).toEqual(Object.keys(catalogs.en.clusters.registration));
    for (const message of Object.values(current))
      expect(() => parseMessage(message as string)).not.toThrow();
    expect(current.hintKubeconfig).toContain("kubeconfig");
    expect(current.hintKubeconfig).toContain("context");
    expect(current.hintServiceAccount).toContain("ca_cert");
    expect(current.hintServiceAccount).toContain("token");
    expect(current.hintExecPlugin).toContain("region + cluster_name");
  });
});
