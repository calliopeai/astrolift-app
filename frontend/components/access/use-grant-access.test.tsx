import { readFileSync } from "node:fs";

import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { act, renderHook, waitFor } from "@testing-library/react";
import { buildSchema, execute, parse, validate } from "graphql";
import { createTranslator, NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import en from "@/messages/en.json";
import es from "@/messages/es.json";
import ja from "@/messages/ja.json";
import { toast } from "sonner";

import type { GrantDraft } from "./GrantAccessFlow";
import { useGrantAccess } from "./use-grant-access";

vi.mock("sonner", () => ({ toast: { warning: vi.fn() } }));
const schema = buildSchema(readFileSync("schema.graphql", "utf8"));
const role = {
  id: "20000000-0000-0000-0000-000000000002",
  slug: "literal-role",
  name: "ACTUAL_ROLE",
  description: "SERVER_ROLE_DESCRIPTION",
  scopeLevel: "APP",
  permissions: ["app.deploy"],
  isSystem: false,
};
const person = {
  kind: "user" as const,
  id: "42",
  name: "ACTUAL_PERSON",
  detail: "person@example.test",
};
const draft: GrantDraft = {
  principals: [person],
  roleId: role.id,
  scope: { kind: "APP", id: "30000000-0000-0000-0000-000000000003", name: "ACTUAL_APP" },
  expiry: { kind: "never" },
};
const preview = {
  ok: true,
  errors: [],
  action: "GRANT",
  permissions: role.permissions,
  scopeKind: "APP",
  scopeGuid: draft.scope!.id,
  sourceScopeLabel: "ACTUAL_APP",
  summary: "SERVER_SUMMARY",
  gainingCount: 401,
  losingCount: 0,
  unchangedCount: 0,
  gaining: [],
  losing: [],
  unchanged: [],
  groups: [],
  allowed: true,
  refusal: null,
  notes: ["SERVER_NOTE"],
};

function fixture(locale: "en" | "es" | "ja" = "es") {
  const requests: Array<{ operationName: string; variables: Record<string, unknown> }> = [];
  let grantResponse: Record<string, unknown> | null = { ok: false, errors: [], data: null };
  type PreviewFixture = Omit<typeof preview, "refusal" | "errors"> & {
    refusal: string | null;
    errors: string[];
  };
  let previewResponse: PreviewFixture | null = preview;
  const fetch = vi.fn(async (_url: RequestInfo | URL, options?: RequestInit) => {
    const request = JSON.parse(String(options?.body));
    requests.push(request);
    const document = parse(request.query);
    expect(validate(schema, document)).toEqual([]);
    const result = await execute({
      schema,
      document,
      variableValues: request.variables,
      rootValue: {
        astroliftRolesICanGrant: [role],
        astroliftNavTree: null,
        astroliftGrantPreview: () => previewResponse,
        grantRole: () => grantResponse,
        astroliftPrincipalSearch: {
          items: [
            {
              kind: "GROUP",
              key: "GROUP:okta:actual",
              name: "Actual IdP group",
              secondary: "",
              userId: null,
              memberId: null,
              lifecycle: null,
              avatarUrl: null,
              groupExternalId: "okta:actual",
              memberCount: 1,
              bindingsCount: 0,
              mappingsCount: 0,
              teamId: null,
              teamSlug: null,
              invitationId: null,
              invitationStatus: null,
              expiresAt: null,
            },
          ],
          totalCount: 1,
          page: 1,
          pageSize: 10,
          counts: [{ kind: "GROUP", count: 1 }],
        },
      },
    });
    expect(result.errors).toBeUndefined();
    return new Response(JSON.stringify(result), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  });
  const client = new ApolloClient({
    cache: new InMemoryCache(),
    link: new HttpLink({ uri: "https://fixture.invalid/app/gql/config/", fetch }),
  });
  const catalogs = { en, es, ja };
  const wrapper = ({ children }: { children: ReactNode }) => (
    <ApolloProvider client={client}>
      <NextIntlClientProvider locale={locale} messages={catalogs[locale]} timeZone="UTC">
        {children}
      </NextIntlClientProvider>
    </ApolloProvider>
  );
  return {
    client,
    wrapper,
    requests,
    t: createTranslator({ locale, messages: catalogs[locale], namespace: "shared.access.grant" }),
    grant: (response: typeof grantResponse) => {
      grantResponse = response;
    },
    preview: (response: typeof previewResponse) => {
      previewResponse = response;
    },
  };
}

beforeEach(() => vi.clearAllMocks());
describe("grant access actual Apollo HTTP boundary", () => {
  it("localizes searched IdP group counts while preserving the query, filters and external identity", async () => {
    const f = fixture();
    const { result } = renderHook(useGrantAccess, { wrapper: f.wrapper });
    await waitFor(() => expect(result.current.roles).toHaveLength(1));
    expect(result.current.roles[0].description).toBe(role.description);
    act(() => result.current.search.setQuery("  okta:actual  "));
    await waitFor(() => expect(result.current.search.results).toHaveLength(1));
    expect(result.current.search.results[0]).toEqual({
      kind: "group",
      id: "okta:actual",
      name: "Actual IdP group",
      detail: f.t("groupDetail", { count: 1 }),
    });
    expect(f.requests.find((r) => r.operationName === "PrincipalSearch")?.variables).toEqual({
      search: "okta:actual",
      filter: { kind: ["USER", "GROUP", "TEAM"] },
      page: 1,
      pageSize: 10,
    });
  });

  it("preserves server preview refusals and supplies a localized fallback only when absent", async () => {
    const f = fixture();
    const { result } = renderHook(useGrantAccess, { wrapper: f.wrapper });
    await waitFor(() => expect(result.current.rolesLoading).toBe(false));
    f.preview({ ...preview, allowed: false, refusal: "ACTUAL_DENIAL: org.manage_members" });
    expect(await result.current.preview(draft)).toMatchObject({
      refusal: "ACTUAL_DENIAL: org.manage_members",
      gainingCount: 401,
      notes: ["SERVER_NOTE"],
    });
    f.preview({ ...preview, allowed: false });
    expect((await result.current.preview(draft)).refusal).toBe(f.t("cannotGrant"));
    f.preview({ ...preview, allowed: false, refusal: "" });
    expect((await result.current.preview(draft)).refusal).toBe(f.t("cannotGrant"));
    f.preview({ ...preview, ok: false, errors: [] });
    await expect(result.current.preview(draft)).rejects.toThrow(f.t("previewFailed"));
    f.preview({ ...preview, ok: false, errors: ["SERVER_VERSION_REFUSAL"] });
    await expect(result.current.preview(draft)).rejects.toThrow("SERVER_VERSION_REFUSAL");
    expect(f.requests.filter((r) => r.operationName === "GrantPreview")[0].variables).toEqual({
      input: {
        action: "GRANT",
        principals: [{ kind: "USER", id: "42" }],
        roleId: role.id,
        scopeKind: "APP",
        scopeId: draft.scope!.id,
        expiresAt: null,
      },
      limit: 50,
    });
    expect(f.requests.some((r) => r.operationName === "GrantRole")).toBe(false);
  });

  it.each(["es", "ja"] as const)(
    "%s keeps a failed grant failed and preserves server messages without refresh or success",
    async (locale) => {
      const f = fixture(locale);
      const { result } = renderHook(useGrantAccess, { wrapper: f.wrapper });
      await waitFor(() => expect(result.current.rolesLoading).toBe(false));
      const refresh = vi.spyOn(f.client, "refetchQueries");
      expect(await result.current.onSubmit(draft)).toEqual([
        { principal: person, ok: false, error: f.t("grantFailed") },
      ]);
      f.grant({
        ok: false,
        errors: [
          { code: "FORBIDDEN", field: "scopeGuid", message: "SERVER_DENIAL: app.deploy {raw}" },
        ],
        data: null,
      });
      expect(await result.current.onSubmit(draft)).toEqual([
        { principal: person, ok: false, error: "SERVER_DENIAL: app.deploy {raw}" },
      ]);
      expect(refresh).not.toHaveBeenCalled();
      expect(toast.warning).not.toHaveBeenCalled();
    }
  );

  it("keeps a successful write successful when refreshing fails and never repeats the grant", async () => {
    const f = fixture();
    f.grant({
      ok: true,
      errors: [],
      data: {
        id: "40000000-0000-0000-0000-000000000004",
        user: { id: person.id, username: person.name, email: person.detail },
        role,
        scopeKind: "APP",
        scopeId: draft.scope!.id,
        sourceScopeLabel: "ACTUAL_APP",
        grantedAt: "2026-09-30T12:00:00Z",
      },
    });
    const { result } = renderHook(useGrantAccess, { wrapper: f.wrapper });
    await waitFor(() => expect(result.current.rolesLoading).toBe(false));
    vi.spyOn(f.client, "refetchQueries").mockRejectedValue(new Error("refresh transport failed"));
    expect(await result.current.onSubmit(draft)).toEqual([{ principal: person, ok: true }]);
    expect(toast.warning).toHaveBeenCalledExactlyOnceWith(f.t("refreshFailed"));
    const writes = f.requests.filter((r) => r.operationName === "GrantRole");
    expect(writes).toHaveLength(1);
    expect(writes[0].variables).toEqual({
      input: {
        userId: person.id,
        roleId: role.id,
        scopeKind: "APP",
        scopeGuid: draft.scope!.id,
        expiresAt: null,
      },
    });
  });

  it("rejects an unsupported token holder in the locale without a mutation", async () => {
    const f = fixture("ja");
    const { result } = renderHook(useGrantAccess, { wrapper: f.wrapper });
    await waitFor(() => expect(result.current.rolesLoading).toBe(false));
    const token = { kind: "token" as const, id: "synthetic-token-id", name: "Literal token name" };
    expect(await result.current.onSubmit({ ...draft, principals: [token] })).toEqual([
      {
        principal: token,
        ok: false,
        error: f.t("cannotHold", { kind: ja.shared.access.principal.token }),
      },
    ]);
    expect(f.requests.some((r) => r.operationName === "GrantRole")).toBe(false);
  });
});
