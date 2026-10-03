import { readFileSync } from "node:fs";
import path from "node:path";
import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { parse, type MessageFormatElement } from "@formatjs/icu-messageformat-parser";
import { act, render, renderHook, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NextIntlClientProvider, createTranslator, useTranslations } from "next-intl";
import { type ReactNode } from "react";
import { hydrateRoot } from "react-dom/client";
import { renderToString } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { locales } from "@/i18n/config";
import { useLocalListState } from "@/components/list/use-list-state";
import { TeamMembersClient } from "@/app/(app)/administration/access/teams/[slug]/team-clients";
import { TeamDetailScreen } from "./TeamDetailScreen";
import { TeamMembersPanel } from "./TeamMembersPanel";
import { useTeamDetail } from "./use-team-detail";
import { useTeamMembers } from "./use-team-members";
import { localizedTeamMembersList } from "./teams-list";
import { TEAM_DETAIL, TEAMS, MEMBERS, ROLES, membersPanelProps } from "./teams.fixtures";
const state = vi.hoisted(() => ({ can: true, success: vi.fn(), warning: vi.fn(), error: vi.fn() }));
vi.mock("sonner", () => ({ toast: state }));
vi.mock("@/lib/permissions/use-my-permissions", () => ({
  useMyPermissions: () => ({ can: () => state.can, loading: false }),
}));
vi.mock("next/navigation", () => ({
  usePathname: () => "/administration/access/teams/platform/members",
  useSearchParams: () => new URLSearchParams(),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));
const catalogs = Object.fromEntries(
  locales.map((l) => [l, JSON.parse(readFileSync(path.resolve("messages", l + ".json"), "utf8"))])
);
function translator(locale: string, namespace = "teams.members") {
  return createTranslator({ locale, messages: catalogs[locale], namespace });
}
const rawRole = {
  ...ROLES[0],
  name: "RAW_ROLE_NAME",
  slug: "literal-role-slug",
  description: "RAW_ROLE_DESCRIPTION",
  permissions: ["literal.permission"],
};
type Mode =
  | "ok"
  | "refused"
  | "missing-error"
  | "transport"
  | "refresh-failed"
  | "refused-refresh-failed"
  | "mixed"
  | "partial"
  | "accepted-no-summary"
  | "roles-failed"
  | "roles-null"
  | "roles-empty"
  | "members-failed"
  | "members-null"
  | "team-failed"
  | "team-null";
type Request = { operationName: string; variables: Record<string, unknown> };
function context(locale: string, mode: Mode = "ok") {
  const requests: Request[] = [];
  let current = mode;
  const counts = new Map<string, number>();
  const fetcher = vi.fn(async (_url: unknown, init?: RequestInit) => {
    const request = JSON.parse(String(init?.body)) as Request;
    requests.push(request);
    const n = (counts.get(request.operationName) ?? 0) + 1;
    counts.set(request.operationName, n);
    let data;
    switch (request.operationName) {
      case "ListTeams":
        if (current === "team-failed") throw new Error("RAW_TEAM_READ_FAILURE");
        data = { astroliftTeams: current === "team-null" ? null : TEAMS };
        break;
      case "ListRoles":
        if (current === "roles-failed") throw new Error("RAW_ROLE_READ_FAILURE");
        data = {
          astroliftRoles:
            current === "roles-null"
              ? null
              : current === "roles-empty"
                ? []
                : [rawRole, { ...ROLES[1], id: "excluded-project", scopeLevel: "PROJECT" }],
        };
        break;
      case "ListTeamMembers":
        if (
          current === "members-failed" ||
          (["refresh-failed", "refused-refresh-failed"].includes(current) && n > 1)
        )
          throw new Error("RAW_MEMBER_READ_FAILURE");
        data = { astroliftTeamMembers: current === "members-null" ? null : MEMBERS };
        break;
      case "BulkAssignAstroliftTeamMemberRoles": {
        if (current === "transport") throw new Error("RAW_TRANSPORT_FAILURE");
        const refused = ["refused", "refused-refresh-failed", "missing-error"].includes(current);
        data = {
          bulkAssignAstroliftTeamMemberRoles: {
            ok: !refused,
            errors:
              current === "missing-error"
                ? []
                : refused
                  ? [{ code: "DENIED", message: "RAW_BIND_REFUSAL", field: "roleId" }]
                  : [],
            data:
              current === "accepted-no-summary"
                ? null
                : {
                    assignedCount: current === "mixed" || current === "partial" ? 1 : 2,
                    alreadyAssignedCount: current === "mixed" ? 1 : 0,
                    failedCount: current === "partial" ? 1 : 0,
                    results: [],
                  },
          },
        };
        break;
      }
      default:
        throw new Error(request.operationName);
    }
    return new Response(JSON.stringify({ data }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  });
  const client = new ApolloClient({
      // Browser-extension discovery schedules a timer beyond this fixture's teardown.
      devtools: { enabled: false },
      cache: new InMemoryCache(),
      link: new HttpLink({
        uri: "https://example.invalid/graphql",
        fetch: fetcher as typeof fetch,
      }),
    }),
    intlErrors = vi.fn();
  const wrapper = ({ children }: { children: ReactNode }) => (
    <NextIntlClientProvider
      locale={locale}
      messages={catalogs[locale]}
      now={new Date("2026-09-30T12:00:00Z")}
      timeZone="America/Costa_Rica"
      onError={intlErrors}
    >
      <ApolloProvider client={client}>{children}</ApolloProvider>
    </NextIntlClientProvider>
  );
  return {
    client,
    requests,
    wrapper,
    intlErrors,
    setMode: (mode: Mode) => {
      current = mode;
    },
  };
}
function flatten(value: Record<string, unknown>, prefix = ""): Record<string, string> {
  return Object.fromEntries(
    Object.entries(value).flatMap(([key, value]) => {
      const name = prefix ? prefix + "." + key : key;
      return typeof value === "string"
        ? [[name, value]]
        : Object.entries(flatten(value as Record<string, unknown>, name));
    })
  );
}
function args(nodes: MessageFormatElement[]): string[] {
  return [
    ...new Set(
      nodes.flatMap((n): string[] => {
        if (n.type === 0 || n.type === 7) return [];
        if (n.type === 8) return ["tag:" + n.value, ...args(n.children)];
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
  state.can = true;
  state.success.mockClear();
  state.warning.mockClear();
  state.error.mockClear();
  window.localStorage.clear();
});
describe("Connected team detail/member locale and actual Apollo outcomes", () => {
  it.each(locales)(
    "%s genuine owned catalog with stable ICU and literal list filter values",
    (locale) => {
      for (const key of ["detail", "members"]) {
        const en = flatten(catalogs.en.teams[key]),
          localized = flatten(catalogs[locale].teams[key]);
        expect(Object.keys(localized)).toEqual(Object.keys(en));
        for (const [key, text] of Object.entries(en))
          expect(args(parse(localized[key]))).toEqual(args(parse(text)));
      }
      const english = flatten(catalogs.en.lists.teamMembersBulk),
        localized = flatten(catalogs[locale].lists.teamMembersBulk);
      expect(Object.keys(localized)).toEqual(Object.keys(english));
      for (const [key, text] of Object.entries(english))
        expect(args(parse(localized[key]))).toEqual(args(parse(text)));
      if (locale !== "en") expect(localized.description).not.toBe(english.description);
      const list = localizedTeamMembersList(translator(locale));
      expect(list.id).toBe("admin.access.team.members");
      expect(list.fields[0].options?.map((o) => o.value)).toEqual([
        "active",
        "invited",
        "suspended",
      ]);
      expect(list.searchPlaceholder).toBe(translator(locale)("search"));
    }
  );
  describe.each([
    "ok",
    "refused",
    "missing-error",
    "transport",
    "refresh-failed",
    "refused-refresh-failed",
    "mixed",
    "partial",
    "accepted-no-summary",
  ] as const)("%s", (mode) => {
    it.each(locales)(
      "%s keeps exact typed inputs and truthful accepted/read outcomes",
      async (locale) => {
        const api = context(locale, mode),
          mt = translator(locale),
          t = translator(locale, "lists.teamMembersBulk"),
          hook = renderHook(() => useTeamMembers(TEAMS[0]), { wrapper: api.wrapper });
        try {
          await waitFor(() => expect(hook.result.current.roleSource.known).toBe(true));
          expect(hook.result.current.roles.map((r) => r.id)).toEqual([rawRole.id]);
          let accepted: boolean | undefined;
          await act(async () => {
            accepted = await hook.result.current.onAssign(rawRole.id, ["m-1", "m-2"]);
          });
          const expectedAccepted = [
            "ok",
            "refresh-failed",
            "mixed",
            "partial",
            "accepted-no-summary",
          ].includes(mode);
          expect(accepted).toBe(expectedAccepted);
          expect(
            api.requests
              .filter((r) => r.operationName === "BulkAssignAstroliftTeamMemberRoles")
              .map((r) => r.variables)
          ).toEqual([
            { input: { teamId: TEAMS[0].id, roleId: rawRole.id, memberIds: ["m-1", "m-2"] } },
          ]);
          expect(
            api.requests
              .filter((r) => r.operationName === "ListTeamMembers")
              .map((r) => r.variables)
          ).toEqual(Array(expectedAccepted ? 2 : 1).fill({ teamId: TEAMS[0].id }));
          if (!expectedAccepted) {
            expect(state.success).not.toHaveBeenCalled();
            expect(state.warning).not.toHaveBeenCalled();
            expect(state.error).toHaveBeenCalledWith(
              t("toasts.allFailed", {
                message:
                  mode === "transport"
                    ? "RAW_TRANSPORT_FAILURE"
                    : mode === "missing-error"
                      ? mt("unknownError")
                      : "RAW_BIND_REFUSAL",
              })
            );
          } else {
            expect(state.error).not.toHaveBeenCalled();
            if (mode === "partial")
              expect(state.warning).toHaveBeenCalledWith(
                t("toasts.partial", { assigned: 1, already: 0, failed: 1 })
              );
            else
              expect(state.success).toHaveBeenCalledWith(
                mode === "mixed"
                  ? t("toasts.mixedIdempotent", { assigned: 1, already: 1 })
                  : mode === "accepted-no-summary"
                    ? mt("accepted")
                    : t("toasts.allOk", { count: 2 })
              );
            if (mode === "refresh-failed") {
              expect(state.warning).toHaveBeenCalledWith(mt("refreshWarning"));

              expect(hook.result.current.rows).toHaveLength(3);
            }
          }
          expect(api.intlErrors).not.toHaveBeenCalled();
        } finally {
          hook.unmount();
          api.client.stop();
        }
      }
    );
  });
  it.each(["roles-failed", "roles-null", "roles-empty"] as const)(
    "%s retains real source state, no inferred role writes",
    async (mode) => {
      const api = context("fr", mode),
        hook = renderHook(() => useTeamMembers(TEAMS[0]), { wrapper: api.wrapper });
      try {
        await waitFor(() => expect(hook.result.current.roleSource.loading).toBe(false));
        expect(hook.result.current.roles).toEqual([]);
        expect(hook.result.current.roleSource.known).toBe(mode === "roles-empty");
        expect(hook.result.current.roleSource.error?.message).toBe(
          mode === "roles-failed" ? "RAW_ROLE_READ_FAILURE" : undefined
        );
        await act(async () => {
          expect(await hook.result.current.onAssign(rawRole.id, ["m-1"])).toBe(false);
        });
        expect(
          api.requests.some((r) => r.operationName === "BulkAssignAstroliftTeamMemberRoles")
        ).toBe(false);
        api.setMode("ok");
        act(() => hook.result.current.roleSource.onRetry());
        await waitFor(() => expect(hook.result.current.roles).toHaveLength(1));
        expect(api.requests.filter((r) => r.operationName === "ListRoles")).toHaveLength(2);
      } finally {
        hook.unmount();
        api.client.stop();
      }
    }
  );
  it.each(["members-failed", "members-null"] as const)(
    "%s exposes unavailable member read, real retry not healthy empty",
    async (mode) => {
      const api = context("ja", mode),
        hook = renderHook(() => useTeamMembers(TEAMS[0]), { wrapper: api.wrapper });
      try {
        await waitFor(() => expect(hook.result.current.error).not.toBeNull());
        expect(hook.result.current.error?.message).toBe(
          mode === "members-failed"
            ? "RAW_MEMBER_READ_FAILURE"
            : translator("ja", "lists.teamMembersBulk")("loadError")
        );
        api.setMode("ok");
        act(() => hook.result.current.onRetry());
        await waitFor(() => expect(hook.result.current.rows).toHaveLength(3));
        expect(hook.result.current.error).toBeNull();
      } finally {
        hook.unmount();
        api.client.stop();
      }
    }
  );
  it.each(["team-failed", "team-null"] as const)(
    "%s actual detail client exposes raw/unknown read and retries",
    async (mode) => {
      const api = context("de", mode),
        t = translator("de", "teams.detail"),
        view = render(<TeamMembersClient slug={TEAMS[0].slug} />, { wrapper: api.wrapper });
      try {
        await screen.findByText(
          mode === "team-failed" ? "RAW_TEAM_READ_FAILURE" : t("unavailable")
        );
        expect(screen.getByText(t("loadFailed"))).toBeInTheDocument();
        expect(screen.queryByText(t("notFound"))).not.toBeInTheDocument();
        api.setMode("ok");
        await userEvent.click(screen.getByRole("button", { name: t("retry") }));
        await screen.findByRole("heading", { name: new RegExp(TEAMS[0].name) });
        await screen.findByText(MEMBERS[0].user.username);
      } finally {
        view.unmount();
        api.client.stop();
      }
    }
  );
  it.each(["roles-failed", "roles-null", "roles-empty"] as const)(
    "%s actual role dialog distinguishes unavailable from empty and retries",
    async (mode) => {
      const api = context("es", mode),
        mt = translator("es"),
        t = translator("es", "lists.teamMembersBulk"),
        view = render(<TeamMembersClient slug={TEAMS[0].slug} />, { wrapper: api.wrapper });
      try {
        await screen.findByText(MEMBERS[0].user.username);
        await userEvent.click(screen.getAllByRole("checkbox")[1]);
        await userEvent.click(
          screen.getByRole("button", { name: t("assignButton", { count: 1 }) })
        );
        await screen.findByText(
          mode === "roles-failed"
            ? "RAW_ROLE_READ_FAILURE"
            : mode === "roles-null"
              ? mt("rolesUnknown")
              : t("assignDialog.noRoles")
        );
        expect(screen.getByRole("button", { name: t("assignDialog.confirmLabel") })).toBeDisabled();
        expect(
          api.requests.some((r) => r.operationName === "BulkAssignAstroliftTeamMemberRoles")
        ).toBe(false);
        if (mode === "roles-failed") {
          expect(screen.queryByText(t("assignDialog.noRoles"))).not.toBeInTheDocument();
          api.setMode("ok");
          await userEvent.click(screen.getByRole("button", { name: mt("rolesRetry") }));
          await screen.findByRole("combobox", { name: t("assignDialog.roleLabel") });
          expect(api.requests.filter((r) => r.operationName === "ListRoles")).toHaveLength(2);
        }
      } finally {
        view.unmount();
        api.client.stop();
      }
    }
  );
  it("cached detail read failure preserves team identity and exposes real retry", async () => {
    const api = context("pt-BR"),
      view = render(<TeamMembersClient slug={TEAMS[0].slug} />, { wrapper: api.wrapper });
    try {
      await screen.findByRole("heading", { name: new RegExp(TEAMS[0].name) });
      api.setMode("team-failed");
      await act(async () => {
        await api.client.refetchQueries({ include: ["ListTeams"] }).catch(() => {});
      });
      // Query cache stays visible while the failed refresh is reported.
      await screen.findByText("RAW_TEAM_READ_FAILURE");
      expect(screen.getByRole("heading", { name: new RegExp(TEAMS[0].name) })).toBeInTheDocument();
      expect(
        screen.queryByText(translator("pt-BR", "teams.detail")("notFound"))
      ).not.toBeInTheDocument();
      api.setMode("ok");
      await userEvent.click(
        screen.getByRole("button", { name: translator("pt-BR", "teams.detail")("retry") })
      );
      await waitFor(() =>
        expect(screen.queryByText("RAW_TEAM_READ_FAILURE")).not.toBeInTheDocument()
      );
    } finally {
      view.unmount();
      api.client.stop();
    }
  });
  it("withdrawn existing team-management decision blocks a selected grant without losing the draft", async () => {
    const api = context("ko"),
      t = translator("ko", "lists.teamMembersBulk"),
      view = render(<TeamMembersClient slug={TEAMS[0].slug} />, { wrapper: api.wrapper });
    try {
      await screen.findByText(MEMBERS[0].user.username);
      await userEvent.click(screen.getAllByRole("checkbox")[1]);
      await userEvent.click(screen.getByRole("button", { name: t("assignButton", { count: 1 }) }));
      await userEvent.click(screen.getByRole("combobox", { name: t("assignDialog.roleLabel") }));
      await userEvent.click(screen.getByRole("option", { name: /RAW_ROLE_NAME/ }));
      state.can = false;
      view.rerender(<TeamMembersClient slug={TEAMS[0].slug} />);
      expect(screen.getByRole("button", { name: t("assignDialog.confirmLabel") })).toBeDisabled();
      expect(screen.getByRole("combobox", { name: t("assignDialog.roleLabel") })).toHaveTextContent(
        "literal-role-slug"
      );
      expect(
        api.requests.some((r) => r.operationName === "BulkAssignAstroliftTeamMemberRoles")
      ).toBe(false);
    } finally {
      view.unmount();
      api.client.stop();
    }
  });
  it("missing team skips both member and role read; read-only skips roles and retains list", async () => {
    const api = context("ko"),
      hook = renderHook(({ team }) => useTeamMembers(team), {
        wrapper: api.wrapper,
        initialProps: { team: null as { id: string } | null },
      });
    try {
      expect(api.requests).toEqual([]);
      expect(hook.result.current.loading).toBe(true);
      state.can = false;
      hook.rerender({ team: TEAMS[0] });
      await waitFor(() => expect(hook.result.current.rows).toHaveLength(3));
      expect(api.requests.map((r) => r.operationName)).toEqual(["ListTeamMembers"]);
      expect(hook.result.current.canManageTeamMembers).toBe(false);
    } finally {
      hook.unmount();
      api.client.stop();
    }
  });
  it.each(["ok", "refused-refresh-failed"] as const)(
    "%s actual click/keyboard parent retains or clears selected draft correctly",
    async (mode) => {
      const api = context("fr", mode),
        t = translator("fr", "lists.teamMembersBulk"),
        view = render(<TeamMembersClient slug={TEAMS[0].slug} />, { wrapper: api.wrapper });
      try {
        await screen.findByText(MEMBERS[0].user.username);
        const boxes = screen.getAllByRole("checkbox");
        await userEvent.click(boxes[1]);
        await userEvent.click(
          screen.getByRole("button", { name: t("assignButton", { count: 1 }) })
        );
        await userEvent.click(screen.getByRole("combobox", { name: t("assignDialog.roleLabel") }));
        await userEvent.click(screen.getByRole("option", { name: /RAW_ROLE_NAME/ }));
        const button = screen.getByRole("button", { name: t("assignDialog.confirmLabel") });
        button.focus();
        await userEvent.keyboard("{Enter}");
        await waitFor(() => expect(mode === "ok" ? state.success : state.error).toHaveBeenCalled());
        if (mode === "ok") expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
        else {
          expect(screen.getByRole("alertdialog")).toBeInTheDocument();
          expect(
            screen.getByRole("combobox", { name: t("assignDialog.roleLabel") })
          ).toHaveTextContent("literal-role-slug");
          expect(state.warning).not.toHaveBeenCalled();
        }
      } finally {
        view.unmount();
        api.client.stop();
      }
    }
  );
});
function FixtureMembers() {
  const mt = useTranslations("teams.members");
  const list = useLocalListState(localizedTeamMembersList(mt));
  return (
    <TeamMembersPanel
      {...membersPanelProps(list, [{ ...MEMBERS[0], joinedAt: "2026-06-03T00:30:00Z" }])}
      list={list}
    />
  );
}
describe("Team pure SSR/request context", () => {
  it.each(locales)(
    "%s SSR→hydrate preserves translated team frame, literals and request timezone date",
    async (locale) => {
      const t = translator(locale, "teams.detail"),
        mt = translator(locale),
        errors = vi.fn(),
        node = (
          <NextIntlClientProvider
            locale={locale}
            messages={catalogs[locale]}
            timeZone="America/Costa_Rica"
            now={new Date("2026-09-30T12:00:00Z")}
          >
            <TeamDetailScreen {...TEAM_DETAIL} tab="members">
              <FixtureMembers />
            </TeamDetailScreen>
          </NextIntlClientProvider>
        ),
        container = document.createElement("div");
      container.innerHTML = renderToString(node);
      document.body.append(container);
      const before = container.textContent;
      const stablePresentation = () => [
        container.querySelector("h1")?.textContent,
        container.querySelector("thead")?.textContent,
        container.querySelector("tbody")?.textContent,
      ];
      const beforePresentation = stablePresentation();
      let root: ReturnType<typeof hydrateRoot> | undefined;
      try {
        expect(before).toContain(t("grant"));
        expect(before).toContain(mt("add"));
        expect(before).toContain(TEAMS[0].name);
        expect(before).toContain(
          new Intl.DateTimeFormat(locale, {
            year: "numeric",
            month: "short",
            day: "numeric",
            timeZone: "America/Costa_Rica",
          }).format(new Date("2026-06-03T00:30:00Z"))
        );
        await act(async () => {
          root = hydrateRoot(container, node, { onRecoverableError: errors });
        });
        expect(stablePresentation()).toEqual(beforePresentation);
        expect(errors).not.toHaveBeenCalled();
        expect(container.querySelector('a[href*="scope=team%3A"]')?.getAttribute("href")).toContain(
          encodeURIComponent(TEAMS[0].id)
        );
      } finally {
        await act(async () => root?.unmount());
        container.remove();
      }
    }
  );
  it("changed team identity leaves no prior team lookup after actual query result", async () => {
    const api = context("es"),
      hook = renderHook(({ slug }) => useTeamDetail(slug), {
        wrapper: api.wrapper,
        initialProps: { slug: TEAMS[0].slug },
      });
    try {
      await waitFor(() => expect(hook.result.current.team?.id).toBe(TEAMS[0].id));
      hook.rerender({ slug: "unknown-future-team" });
      expect(hook.result.current.team).toBeNull();
      expect(hook.result.current.error).toBeNull();
    } finally {
      hook.unmount();
      api.client.stop();
    }
  });
});
