import { readFileSync } from "node:fs";
import path from "node:path";
import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider, useQuery } from "@apollo/client/react";
import { parse, type MessageFormatElement } from "@formatjs/icu-messageformat-parser";
import { act, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createTranslator, NextIntlClientProvider } from "next-intl";
import { useState, type ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { locales } from "@/i18n/config";
import { LIST_ROLE_BINDINGS_PAGE, LIST_MEMBERS_PAGE } from "@/graphql/identity/identity.queries";
import { AssignmentsTab } from "@/app/(app)/administration/permissions/assignments-tab";
import { GrantRoleDialog, GrantRoleSheet } from "./GrantRoleDialog";
import { grantRoleProps } from "./members.fixtures";
import { useGrantRole } from "./use-grant-role";
const state = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  org: true,
  loading: false,
  orgError: undefined as Error | undefined,
  can: true,
}));
vi.mock("sonner", () => ({ toast: state }));
vi.mock("@/graphql/identity/identity.hooks", () => ({
  useActiveOrg: () => ({
    org: state.org ? { id: "org-id", slug: "literal-org", name: "RAW_ORG_NAME" } : null,
    loading: state.loading,
    error: state.orgError,
  }),
}));
vi.mock("@/lib/permissions/use-my-permissions", () => ({
  useMyPermissions: () => ({ can: () => state.can, loading: false }),
}));
vi.mock("next/navigation", () => ({
  usePathname: () => "/administration/permissions",
  useSearchParams: () => new URLSearchParams("q=literal-search&page=2&size=25"),
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));
const catalogs = Object.fromEntries(
  locales.map((locale) => [
    locale,
    JSON.parse(readFileSync(path.resolve("messages", `${locale}.json`), "utf8")),
  ])
);
const tFor = (locale: string) =>
  createTranslator({
    locale,
    messages: catalogs[locale],
    namespace: "shared.access.legacyGrantRole",
  });
const role = {
  id: "role-guid",
  name: "RAW_CUSTOM_ROLE",
  slug: "literal-custom-role",
  scopeLevel: "ORG" as const,
  permissions: ["RAW_PERMISSION"],
  description: "RAW_CUSTOM_DESCRIPTION",
  isSystem: false,
};
const team = {
  id: "team-guid",
  slug: "literal-team",
  name: "RAW_TEAM_NAME",
  createdAt: "2026-01-01T00:00:00Z",
  updatedAt: "2026-01-01T00:00:00Z",
  deletedAt: null,
  organization: { id: "org-id", name: "RAW_ORG_NAME", slug: "literal-org" },
};
const project = {
  organization: team.organization,
  id: "project-guid",
  slug: "literal-project",
  name: "RAW_PROJECT_NAME",
  createdAt: team.createdAt,
  updatedAt: team.updatedAt,
  deletedAt: null,
  team,
};
const vars = { search: "EXACT_SEARCH", limit: 25, after: "EXACT_CURSOR" };
type Mode =
  | "ok"
  | "refused"
  | "missing"
  | "transport"
  | "refresh-failed"
  | "refused-refresh-failed"
  | "scope-failed"
  | "roles-failed"
  | "roles-empty";
type Request = { operationName: string; variables: Record<string, unknown> };
function context(locale: string, mode: Mode = "ok") {
  const requests: Request[] = [];
  let currentMode = mode;
  const counts = new Map<string, number>();
  const fetcher = vi.fn(async (_url: unknown, init?: RequestInit) => {
    const r = JSON.parse(String(init?.body)) as Request;
    requests.push(r);
    counts.set(r.operationName, (counts.get(r.operationName) ?? 0) + 1);
    let data;
    if (r.operationName === "GrantRole") {
      if (currentMode === "transport") throw new Error("RAW_TRANSPORT_FAILURE");
      const refused = ["refused", "refused-refresh-failed", "missing"].includes(currentMode);
      data = {
        grantRole: {
          ok: !refused,
          errors: ["refused", "refused-refresh-failed"].includes(currentMode)
            ? [{ code: "DENIED", message: "RAW_TARGET_SCOPE_REFUSAL", field: "scopeGuid" }]
            : [],
          data: null,
        },
      };
    } else if (r.operationName === "ListTeams") {
      if (currentMode === "scope-failed") throw new Error("RAW_SCOPE_READ_FAILURE");
      data = { astroliftTeams: [team] };
    } else if (r.operationName === "ListProjects") data = { astroliftProjects: [project] };
    else if (r.operationName === "ListRoles") {
      if (currentMode === "roles-failed") throw new Error("RAW_ROLE_READ_FAILURE");
      data = { astroliftRoles: currentMode === "roles-empty" ? [] : [role] };
    } else if (
      r.operationName === "ListRoleBindingsPage" ||
      r.operationName === "ListMembersPage"
    ) {
      if (
        ["refresh-failed", "refused-refresh-failed"].includes(currentMode) &&
        counts.get(r.operationName)! > 1
      )
        throw new Error("RAW_REFRESH_FAILURE");
      data = {
        [r.operationName === "ListRoleBindingsPage"
          ? "astroliftRoleBindingsPage"
          : "astroliftMembersPage"]: {
          items: [],
          totalCount: 0,
          nextCursor: null,
          page: 2,
          pageSize: 25,
        },
      };
    } else throw new Error(r.operationName);
    return new Response(JSON.stringify({ data }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  });
  const client = new ApolloClient({
      cache: new InMemoryCache(),
      link: new HttpLink({
        uri: "https://example.invalid/graphql",
        fetch: fetcher as typeof fetch,
      }),
    }),
    errors = vi.fn();
  const wrapper = ({ children }: { children: ReactNode }) => (
    <NextIntlClientProvider
      locale={locale}
      messages={catalogs[locale]}
      timeZone="Asia/Tokyo"
      now={new Date("2026-09-30T12:00:00Z")}
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
function Grant({ scope = "ORG" as "ORG" | "TEAM" | "PROJECT" | "APP" }) {
  const [open, setOpen] = useState(true);
  useQuery(LIST_ROLE_BINDINGS_PAGE, { variables: vars, fetchPolicy: "network-only" });
  useQuery(LIST_MEMBERS_PAGE, { variables: vars, fetchPolicy: "network-only" });
  return (
    <>
      <GrantRoleDialog
        open={open}
        onOpenChange={setOpen}
        roles={[{ ...role, scopeLevel: scope }]}
        initialUserId="42"
        initialUserLabel="RAW_USER_LABEL"
      />
      <output data-testid="open">{String(open)}</output>
    </>
  );
}
async function chooseRole(t: ReturnType<typeof tFor>) {
  await userEvent.click(screen.getByRole("combobox", { name: t("role") }));
  await userEvent.click(await screen.findByRole("option", { name: /RAW_CUSTOM_ROLE/ }));
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
function args(nodes: MessageFormatElement[]): string[] {
  return [
    ...new Set(
      nodes.flatMap((n): string[] => {
        if (n.type === 0 || n.type === 7) return [];
        if (n.type === 8) return [`tag:${n.value}`, ...args(n.children)];
        if (n.type === 5 || n.type === 6)
          return [
            `${n.type}:${n.value}`,
            ...Object.values(n.options).flatMap((o) => args(o.value)),
          ];
        return [`${n.type}:${n.value}`];
      })
    ),
  ].sort();
}
beforeEach(() => {
  state.org = true;
  state.loading = false;
  state.orgError = undefined;
  state.can = true;
  state.success.mockClear();
  state.error.mockClear();
  state.warning.mockClear();
});
describe("Legacy role grant actual context and translated outcomes", () => {
  it.each(locales)(
    "%s preserves ICU arguments, literal scopes and custom role metadata",
    (locale) => {
      const english = flatten(catalogs.en.shared.access.legacyGrantRole),
        translated = flatten(catalogs[locale].shared.access.legacyGrantRole);
      expect(Object.keys(translated).sort()).toEqual(Object.keys(english).sort());
      for (const [key, text] of Object.entries(english))
        expect(args(parse(translated[key]))).toEqual(args(parse(text)));
      if (locale !== "en") expect(translated.description).not.toBe(english.description);
      expect(translated.description).not.toMatch(/SCIM/);
    }
  );
  describe.each([
    "ok",
    "refused",
    "missing",
    "transport",
    "refresh-failed",
    "refused-refresh-failed",
  ] as const)("%s", (mode) => {
    it.each(locales)(
      "%s binds exact IDs and closes only accepted writes, with current page refresh variables",
      async (locale) => {
        const api = context(locale, mode),
          t = tFor(locale),
          view = render(<Grant />, { wrapper: api.wrapper });
        try {
          await waitFor(() =>
            expect(
              api.requests.filter((r) => r.operationName === "ListRoleBindingsPage")
            ).toHaveLength(1)
          );
          await chooseRole(t);
          expect(screen.getByLabelText(t("userId"))).toHaveAttribute("readonly");
          expect(screen.getByText(t("targetUser", { name: "RAW_USER_LABEL" }))).toBeInTheDocument();
          await userEvent.click(screen.getByRole("button", { name: t("title") }));
          const accepted = mode === "ok" || mode === "refresh-failed";
          await waitFor(() => expect(accepted ? state.success : state.error).toHaveBeenCalled());
          expect(screen.getByTestId("open")).toHaveTextContent(String(!accepted));
          expect(
            api.requests.filter((r) => r.operationName === "GrantRole").map((r) => r.variables)
          ).toEqual([
            { input: { userId: "42", roleId: role.id, scopeKind: "ORG", scopeGuid: "org-id" } },
          ]);
          if (accepted) {
            expect(state.success).toHaveBeenCalledWith(t("granted"));
            expect(state.error).not.toHaveBeenCalled();
            for (const name of ["ListRoleBindingsPage", "ListMembersPage"])
              expect(
                api.requests.filter((r) => r.operationName === name).map((r) => r.variables)
              ).toEqual([vars, vars]);
            if (mode === "refresh-failed")
              expect(state.warning).toHaveBeenCalledWith(t("refreshWarning"));
          } else {
            expect(state.success).not.toHaveBeenCalled();
            expect(state.warning).not.toHaveBeenCalled();
            expect(state.error).toHaveBeenCalledWith(
              mode === "transport"
                ? "RAW_TRANSPORT_FAILURE"
                : mode === "missing"
                  ? t("failed")
                  : "RAW_TARGET_SCOPE_REFUSAL"
            );
            expect(screen.getByLabelText(t("userId"))).toHaveValue(42);
            expect(screen.getByRole("combobox", { name: t("role") })).toHaveTextContent(role.name);
            expect(
              api.requests.filter((r) => r.operationName === "ListRoleBindingsPage")
            ).toHaveLength(1);
          }
          expect(api.errors).not.toHaveBeenCalled();
        } finally {
          view.unmount();
          api.client.stop();
        }
      }
    );
  });
  it.each(["TEAM", "PROJECT"] as const)(
    "%s submits actual selected scope without inferred global authority",
    async (scope) => {
      const api = context("ja", "refused"),
        t = tFor("ja"),
        view = render(<Grant scope={scope} />, { wrapper: api.wrapper });
      try {
        await chooseRole(t);
        await waitFor(() => expect(screen.getByRole("button", { name: t("title") })).toBeEnabled());
        await userEvent.click(screen.getByRole("button", { name: t("title") }));
        await waitFor(() => expect(state.error).toHaveBeenCalledWith("RAW_TARGET_SCOPE_REFUSAL"));
        expect(api.requests.find((r) => r.operationName === "GrantRole")!.variables).toEqual({
          input: {
            userId: "42",
            roleId: role.id,
            scopeKind: scope,
            scopeGuid: scope === "TEAM" ? team.id : project.id,
          },
        });
        expect(screen.getByTestId("open")).toHaveTextContent("true");
      } finally {
        view.unmount();
        api.client.stop();
      }
    }
  );
  it("APP target is unsupported and does not submit invented identity", async () => {
    const api = context("de"),
      t = tFor("de"),
      view = render(<Grant scope="APP" />, { wrapper: api.wrapper });
    try {
      await chooseRole(t);
      expect(screen.getByText(t("unsupportedApp"))).toBeInTheDocument();
      expect(screen.getByRole("button", { name: t("title") })).toBeDisabled();
      expect(api.requests.some((r) => r.operationName === "GrantRole")).toBe(false);
    } finally {
      view.unmount();
      api.client.stop();
    }
  });
  it("current no-org state skips scope reads and refuses direct grants", async () => {
    state.org = false;
    const api = context("ko"),
      hook = renderHook(() => useGrantRole(), { wrapper: api.wrapper });
    try {
      await act(async () => {
        expect(
          await hook.result.current.onGrant({
            userId: "42",
            roleId: role.id,
            scopeKind: "TEAM",
            scopeGuid: team.id,
          })
        ).toBe(false);
      });
      expect(api.requests).toEqual([]);
      expect(state.success).not.toHaveBeenCalled();
    } finally {
      hook.unmount();
      api.client.stop();
    }
  });
  it("scope read failure stays explicit and retries actual target read", async () => {
    const api = context("fr", "scope-failed"),
      t = tFor("fr"),
      view = render(<Grant scope="TEAM" />, { wrapper: api.wrapper });
    try {
      await chooseRole(t);
      await screen.findByText("RAW_SCOPE_READ_FAILURE");
      expect(screen.getByRole("button", { name: t("title") })).toBeDisabled();
      api.setMode("ok");
      await userEvent.click(screen.getByRole("button", { name: t("retry") }));
      await waitFor(() => expect(screen.getByRole("button", { name: t("title") })).toBeEnabled());
      expect(api.requests.filter((r) => r.operationName === "ListTeams")).toHaveLength(2);
      expect(api.requests.some((r) => r.operationName === "GrantRole")).toBe(false);
    } finally {
      view.unmount();
      api.client.stop();
    }
  });
  it.each(["loading", "error", "unknown"] as const)(
    "%s scope facts remain disabled and distinct",
    async (status) => {
      const api = context("es"),
        t = tFor("es"),
        props = grantRoleProps({
          roles: [{ ...role, scopeLevel: "TEAM" }],
          initialUserId: "42",
          scopeReads: {
            TEAM: {
              loading: status === "loading",
              error: status === "error" ? new Error("RAW_READ_ERROR") : undefined,
              known: status !== "unknown",
            },
            PROJECT: { loading: false, error: undefined, known: true },
          },
        }),
        view = render(<GrantRoleSheet {...props} />, { wrapper: api.wrapper });
      try {
        await chooseRole(t);
        expect(
          screen.getByText(
            t(
              status === "error"
                ? "scopeReadFailed"
                : status === "loading"
                  ? "loadingScopes"
                  : "unknownScopes"
            )
          )
        ).toBeInTheDocument();
        expect(screen.getByRole("button", { name: t("title") })).toBeDisabled();
      } finally {
        view.unmount();
        api.client.stop();
      }
    }
  );
  it.each(locales)(
    "%s actual role read failure blocks grants and retries the source",
    async (locale) => {
      const api = context(locale, "roles-failed"),
        t = tFor(locale),
        view = render(<AssignmentsTab />, { wrapper: api.wrapper });
      try {
        await waitFor(() => expect(screen.getByRole("button", { name: t("title") })).toBeEnabled());
        await userEvent.click(screen.getByRole("button", { name: t("title") }));
        await screen.findByText("RAW_ROLE_READ_FAILURE");
        expect(screen.getByText(t("rolesReadFailed"))).toBeInTheDocument();
        expect(screen.getByRole("combobox", { name: t("role") })).toBeDisabled();
        expect(screen.getByRole("button", { name: t("title") })).toBeDisabled();
        expect(screen.queryByText(t("emptyRoles"))).not.toBeInTheDocument();
        expect(api.requests.some((r) => r.operationName === "GrantRole")).toBe(false);
        api.setMode("ok");
        await userEvent.click(screen.getByRole("button", { name: t("retry") }));
        await waitFor(() =>
          expect(screen.getByRole("combobox", { name: t("role") })).toBeEnabled()
        );
        expect(screen.queryByText("RAW_ROLE_READ_FAILURE")).not.toBeInTheDocument();
        expect(api.requests.filter((r) => r.operationName === "ListRoles")).toHaveLength(2);
        fireEvent.change(screen.getByLabelText(t("userId")), { target: { value: "42" } });
        await chooseRole(t);
        await userEvent.click(screen.getByRole("button", { name: t("title") }));
        await waitFor(() => expect(state.success).toHaveBeenCalledWith(t("granted")));
        expect(api.errors).not.toHaveBeenCalled();
      } finally {
        view.unmount();
        api.client.stop();
      }
    }
  );
  it("actual confirmed empty role list is distinct from an unavailable source", async () => {
    const api = context("ja", "roles-empty"),
      t = tFor("ja"),
      view = render(<AssignmentsTab />, { wrapper: api.wrapper });
    try {
      await waitFor(() => expect(screen.getByRole("button", { name: t("title") })).toBeEnabled());
      await userEvent.click(screen.getByRole("button", { name: t("title") }));
      expect(screen.getByText(t("emptyRoles"))).toBeInTheDocument();
      expect(screen.queryByText(t("unknownRoles"))).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: t("title") })).toBeDisabled();
      expect(api.requests.some((r) => r.operationName === "GrantRole")).toBe(false);
    } finally {
      view.unmount();
      api.client.stop();
    }
  });
  it.each(["loading", "unknown"] as const)(
    "%s role source cannot submit cached selection",
    async (status) => {
      const api = context("de"),
        t = tFor("de"),
        props = grantRoleProps({ roles: [role], initialUserId: "42" });
      const view = render(<GrantRoleSheet {...props} />, { wrapper: api.wrapper });
      try {
        await chooseRole(t);
        expect(screen.getByRole("button", { name: t("title") })).toBeEnabled();
        view.rerender(
          <GrantRoleSheet
            {...props}
            rolesLoading={status === "loading"}
            rolesKnown={status !== "unknown"}
          />
        );
        expect(
          screen.getByText(t(status === "loading" ? "loadingRoles" : "unknownRoles"))
        ).toBeInTheDocument();
        expect(screen.getByRole("combobox", { name: t("role") })).toBeDisabled();
        expect(screen.getByRole("button", { name: t("title") })).toBeDisabled();
      } finally {
        view.unmount();
        api.client.stop();
      }
    }
  );
  it("actual parent permission gate hides grant action without making a grant request", async () => {
    state.can = false;
    const api = context("en"),
      view = render(<AssignmentsTab />, { wrapper: api.wrapper });
    try {
      await waitFor(() =>
        expect(api.requests.some((r) => r.operationName === "ListRoles")).toBe(true)
      );
      expect(screen.queryByRole("button", { name: "Grant role" })).not.toBeInTheDocument();
      expect(api.requests.some((r) => r.operationName === "GrantRole")).toBe(false);
    } finally {
      view.unmount();
      api.client.stop();
    }
  });
  it("actual assignments close refresh is redundant: accepted grant refreshes the exact active page once, cancel does no read", async () => {
    const api = context("en"),
      view = render(<AssignmentsTab />, { wrapper: api.wrapper });
    try {
      await waitFor(() => expect(screen.getByRole("button", { name: "Grant role" })).toBeEnabled());
      const initial = api.requests.find((r) => r.operationName === "ListRoleBindingsPage")!;
      await userEvent.click(screen.getByRole("button", { name: "Grant role" }));
      await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
      expect(api.requests.filter((r) => r.operationName === "ListRoleBindingsPage")).toHaveLength(
        1
      );
      await userEvent.click(screen.getByRole("button", { name: "Grant role" }));
      fireEvent.change(screen.getByLabelText("User ID"), { target: { value: "42" } });
      await chooseRole(tFor("en"));
      await userEvent.click(screen.getByRole("button", { name: "Grant role" }));
      await waitFor(() => expect(state.success).toHaveBeenCalled());
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      expect(
        api.requests
          .filter((r) => r.operationName === "ListRoleBindingsPage")
          .map((r) => r.variables)
      ).toEqual([initial.variables, initial.variables]);
    } finally {
      view.unmount();
      api.client.stop();
    }
  });
  it.each(["loading", "error", "missing"] as const)(
    "%s organization facts disable a mounted sheet without inventing targets",
    async (status) => {
      const api = context("pt-BR"),
        t = tFor("pt-BR"),
        props = grantRoleProps({
          roles: [role],
          initialUserId: "42",
          org: status === "missing" ? null : grantRoleProps().org,
          organizationState: {
            loading: status === "loading",
            error: status === "error" ? new Error("RAW_ORG_READ_ERROR") : undefined,
          },
        }),
        view = render(<GrantRoleSheet {...props} />, { wrapper: api.wrapper });
      try {
        await chooseRole(t);
        expect(
          screen.getByText(
            t(status === "loading" ? "loadingOrg" : status === "error" ? "orgReadFailed" : "noOrg")
          )
        ).toBeInTheDocument();
        expect(screen.getByRole("button", { name: t("title") })).toBeDisabled();
      } finally {
        view.unmount();
        api.client.stop();
      }
    }
  );
  it("locale changes preserve typed user ID and selected role/scope; changed current org invalidates stale selection", async () => {
    const api = context("en"),
      props = grantRoleProps({ roles: [role] }),
      wrap = (locale: string, org = props.org) => (
        <NextIntlClientProvider locale={locale} messages={catalogs[locale]} timeZone="UTC">
          <GrantRoleSheet {...props} org={org} />
        </NextIntlClientProvider>
      ),
      view = render(wrap("en"));
    try {
      fireEvent.change(screen.getByLabelText("User ID"), { target: { value: "42" } });
      await chooseRole(tFor("en"));
      view.rerender(wrap("ja"));
      expect(screen.getByLabelText(tFor("ja")("userId"))).toHaveValue(42);
      expect(screen.getByRole("combobox", { name: tFor("ja")("role") })).toHaveTextContent(
        role.name
      );
      expect(screen.getByRole("button", { name: tFor("ja")("title") })).toBeEnabled();
      view.rerender(wrap("ja", { ...props.org!, id: "CHANGED_ORG_GUID" }));
      expect(screen.getByRole("button", { name: tFor("ja")("title") })).toBeDisabled();
    } finally {
      view.unmount();
      api.client.stop();
    }
  });
});
