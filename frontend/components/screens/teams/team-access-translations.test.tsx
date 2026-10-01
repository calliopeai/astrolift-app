import { readFileSync } from "node:fs";
import path from "node:path";
import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider, useQuery } from "@apollo/client/react";
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
import { LIST_ROLE_BINDINGS_PAGE } from "@/graphql/identity/identity.queries";
import { LIST_GROUP_ROLE_MAPPINGS_PAGE } from "@/graphql/access/access.queries";
import { useEntityAccess } from "../administration/access/use-entity-access";
import { EntityAccessPanel } from "../administration/access/EntityAccessPanel";
import {
  localizedEntityAccessList,
  type AccessEntry,
} from "../administration/access/entity-access";
import {
  TEAM_ACCESS,
  APP_ACCESS,
  entityAccessProps,
} from "../administration/access/principal.fixtures";
import { TeamAccessClient } from "@/app/(app)/administration/access/teams/[slug]/team-clients";
import { TeamAccessPanel } from "./TeamAccessPanel";
import { useTeamProjects } from "./use-team-projects";
import { TEAMS, PROJECTS } from "./teams.fixtures";
const state = vi.hoisted(() => ({ can: true, success: vi.fn(), warning: vi.fn(), error: vi.fn() }));
vi.mock("sonner", () => ({ toast: state }));
vi.mock("@/lib/permissions/use-my-permissions", () => ({
  useMyPermissions: () => ({ can: () => state.can, loading: false }),
}));
vi.mock("next/navigation", () => ({
  usePathname: () => "/administration/access/teams/platform",
  useSearchParams: () => new URLSearchParams("q=LITERAL_SEARCH&page=2&size=25"),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));
const catalogs = Object.fromEntries(
  locales.map((l) => [l, JSON.parse(readFileSync(path.resolve("messages", l + ".json"), "utf8"))])
);
function tFor(locale: string, namespace = "shared.access.entityPanel") {
  return createTranslator({ locale, messages: catalogs[locale], namespace });
}
const target = { kind: "TEAM" as const, id: TEAMS[0].id };
const entries = {
  binding: {
    ...TEAM_ACCESS[1],
    role: {
      ...TEAM_ACCESS[1].role!,
      slug: "literal-role",
      name: "RAW_ROLE_NAME",
      description: "RAW_ROLE_DESCRIPTION",
      permissions: ["literal.permission"],
    },
  },
  mapping: {
    ...TEAM_ACCESS[3],
    role: {
      ...TEAM_ACCESS[3].role!,
      slug: "literal-role",
      name: "RAW_ROLE_NAME",
      description: "RAW_ROLE_DESCRIPTION",
      permissions: ["literal.permission"],
    },
  },
  share: { ...APP_ACCESS[APP_ACCESS.length - 1] },
  future: { ...TEAM_ACCESS[1], source: "FUTURE_SOURCE" },
};
type Mode =
  | "ok"
  | "refused"
  | "missing-error"
  | "transport"
  | "refresh-failed"
  | "refused-refresh-failed"
  | "access-failed"
  | "access-null"
  | "project-failed"
  | "project-null"
  | "target-wait";
type Request = { operationName: string; variables: Record<string, unknown> };
function context(
  locale: string,
  source: "binding" | "mapping" | "share" | "future" = "binding",
  mode: Mode = "ok"
) {
  const requests: Request[] = [];
  let current = mode;
  let release: (() => void) | undefined;
  const counts = new Map<string, number>();
  const fetcher = vi.fn(async (_url: unknown, init?: RequestInit) => {
    const request = JSON.parse(String(init?.body)) as Request;
    requests.push(request);
    const n = (counts.get(request.operationName) ?? 0) + 1;
    counts.set(request.operationName, n);
    let data;
    if (request.operationName === "AccessOn") {
      if (current === "target-wait" && request.variables.scopeId === "new-team")
        await new Promise<void>((resolve) => {
          release = resolve;
        });
      if (
        current === "access-failed" ||
        (["refresh-failed", "refused-refresh-failed"].includes(current) && n > 1)
      )
        throw new Error("RAW_ACCESS_READ_FAILURE");
      const entry = entries[source];
      data = {
        astroliftAccessOn:
          current === "access-null"
            ? null
            : {
                items:
                  request.variables.scopeId === "new-team"
                    ? []
                    : [
                        {
                          user: null,
                          memberId: null,
                          groupExternalId: null,
                          groupMemberCount: null,
                          teamId: null,
                          teamSlug: null,
                          teamName: null,

                          accessLevel: null,
                          shareId: null,
                          scopeGuid: TEAMS[0].id,
                          inherits: false,
                          expiresAt: null,
                          ...entry,
                        },
                      ],
                totalCount: 1,
                page: 2,
                pageSize: 25,
              },
      };
    } else if (
      request.operationName === "RevokeRoleBinding" ||
      request.operationName === "DeleteGroupRoleMapping"
    ) {
      if (current === "transport") throw new Error("RAW_REMOVE_TRANSPORT");
      const refused = ["refused", "refused-refresh-failed", "missing-error"].includes(current);
      data = {
        [request.operationName === "RevokeRoleBinding"
          ? "revokeRoleBinding"
          : "deleteGroupRoleMapping"]: {
          ok: !refused,
          errors:
            current === "missing-error"
              ? []
              : refused
                ? [{ code: "DENIED", message: "RAW_REMOVE_REFUSAL", field: "id" }]
                : [],
          data: { id: entries[source].bindingId, deleted: true },
        },
      };
    } else if (
      request.operationName === "ListRoleBindingsPage" ||
      request.operationName === "ListGroupRoleMappingsPage"
    ) {
      if (["refresh-failed", "refused-refresh-failed"].includes(current) && n > 1)
        throw new Error("RAW_ANCESTOR_READ_FAILURE");
      data = {
        [request.operationName === "ListRoleBindingsPage"
          ? "astroliftRoleBindingsPage"
          : "astroliftGroupRoleMappingsPage"]: {
          items: [],
          totalCount: 0,
          page: 2,
          pageSize: 25,
          nextCursor: null,
        },
      };
    } else if (request.operationName === "ListTeams") data = { astroliftTeams: TEAMS };
    else if (request.operationName === "ListProjects") {
      if (current === "project-failed") throw new Error("RAW_PROJECT_READ_FAILURE");
      data = { astroliftProjects: current === "project-null" ? null : PROJECTS };
    } else throw new Error(request.operationName);
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
    intlErrors = vi.fn();
  function Observers() {
    useQuery(LIST_ROLE_BINDINGS_PAGE, {
      variables: { search: "EXACT_BINDING_SEARCH", page: 2, pageSize: 25 },
      fetchPolicy: "network-only",
    });
    useQuery(LIST_GROUP_ROLE_MAPPINGS_PAGE, {
      variables: {
        search: "EXACT_MAPPING_SEARCH",
        groupExternalId: "LITERAL_GROUP",
        page: 2,
        pageSize: 25,
      },
      fetchPolicy: "network-only",
    });
    return null;
  }
  const wrapper = ({ children }: { children: ReactNode }) => (
    <NextIntlClientProvider
      locale={locale}
      messages={catalogs[locale]}
      timeZone="America/Costa_Rica"
      now={new Date("2026-09-30T12:00:00Z")}
      onError={intlErrors}
    >
      <ApolloProvider client={client}>
        <Observers />
        {children}
      </ApolloProvider>
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
    release: () => release?.(),
  };
}
function ActualAccess() {
  const t = useTranslations("teams.access");
  return <EntityAccessPanel {...useEntityAccess(target, t("subject", { name: TEAMS[0].slug }))} />;
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
describe("Team access removal and source contexts", () => {
  it.each(locales)("%s owned ICU parity, source/role identities and scope guidance", (locale) => {
    for (const [english, localized] of [
      [catalogs.en.shared.access.entityPanel, catalogs[locale].shared.access.entityPanel],
      [catalogs.en.teams.access, catalogs[locale].teams.access],
    ]) {
      const en = flatten(english),
        messages = flatten(localized);
      expect(Object.keys(messages)).toEqual(Object.keys(en));
      for (const [key, text] of Object.entries(en))
        expect(args(parse(messages[key]))).toEqual(args(parse(text)));
      if (locale !== "en") expect(Object.values(messages)).not.toEqual(Object.values(en));
    }
    expect(localizedEntityAccessList(tFor(locale)).id).toBe("admin.access.entity");
  });
  describe.each(["binding", "mapping"] as const)("%s", (source) => {
    describe.each(["ok", "refused-refresh-failed", "refresh-failed"] as const)("%s", (mode) => {
      it.each(locales)(
        "%s actual click/keyboard removal keeps raw refused confirmation, or accepts with failed refresh",
        async (locale) => {
          const api = context(locale, source, mode),
            t = tFor(locale),
            listT = tFor(locale, "shared.list"),
            view = render(<ActualAccess />, { wrapper: api.wrapper });
          try {
            await screen.findByText("RAW_ROLE_NAME");
            const originalReads = api.requests.filter((r) => r.operationName === "AccessOn");
            expect(originalReads).toHaveLength(1);
            await userEvent.click(
              screen.getByRole("button", { name: listT("rowActions", { label: t("label") }) })
            );
            await userEvent.click(
              screen.getByRole("menuitem", {
                name: t(source === "binding" ? "removeGrant" : "removeMapping"),
              })
            );
            const principal =
              source === "binding"
                ? entries.binding.user!.username
                : entries.mapping.groupExternalId!;
            expect(screen.getByRole("alertdialog")).toHaveTextContent(
              t("removeTitle", { role: "literal-role", name: principal })
            );
            expect(screen.getByRole("alertdialog")).toHaveTextContent("acme");
            expect(screen.getByRole("alertdialog")).toHaveTextContent(t("otherGrants"));
            if (source === "mapping")
              expect(screen.getByRole("alertdialog")).toHaveTextContent(t("groupLoss"));
            const button = screen.getByRole("button", { name: t("remove") });
            button.focus();
            await userEvent.keyboard("{Enter}");
            const accepted = mode !== "refused-refresh-failed";
            await waitFor(() => expect(accepted ? state.success : state.error).toHaveBeenCalled());
            expect(
              api.requests
                .filter((r) =>
                  ["RevokeRoleBinding", "DeleteGroupRoleMapping"].includes(r.operationName)
                )
                .map((r) => ({ name: r.operationName, variables: r.variables }))
            ).toEqual([
              {
                name: source === "binding" ? "RevokeRoleBinding" : "DeleteGroupRoleMapping",
                variables: { input: { id: entries[source].bindingId } },
              },
            ]);
            if (accepted) {
              expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
              expect(state.success).toHaveBeenCalledWith(t("removed", { role: "literal-role" }));
              expect(state.error).not.toHaveBeenCalled();
              if (mode === "refresh-failed")
                expect(state.warning).toHaveBeenCalledWith(t("refreshWarning"));
            } else {
              expect(screen.getByRole("alertdialog")).toBeInTheDocument();
              expect(state.error).toHaveBeenCalledWith("RAW_REMOVE_REFUSAL");
              expect(state.success).not.toHaveBeenCalled();
              expect(state.warning).not.toHaveBeenCalled();
            }
            for (const name of ["AccessOn", "ListRoleBindingsPage", "ListGroupRoleMappingsPage"]) {
              const calls = api.requests.filter((r) => r.operationName === name);
              expect(calls).toHaveLength(accepted ? 2 : 1);
              if (accepted) expect(calls[1].variables).toEqual(calls[0].variables);
            }
            expect(api.intlErrors).not.toHaveBeenCalled();
          } finally {
            view.unmount();
            api.client.stop();
          }
        }
      );
    });
    it.each(["transport", "missing-error"] as const)(
      "%s actual hook preserves server/transport literal or translated fallback",
      async (mode) => {
        const api = context("ja", source, mode),
          hook = renderHook(() => useEntityAccess(target, "LITERAL_SUBJECT"), {
            wrapper: api.wrapper,
          });
        try {
          await waitFor(() => expect(hook.result.current.rows).toHaveLength(1));
          await act(async () => {
            await expect(hook.result.current.onRemove(hook.result.current.rows[0])).rejects.toThrow(
              mode === "transport" ? "RAW_REMOVE_TRANSPORT" : tFor("ja")("removeFailed")
            );
          });
          expect(api.requests.filter((r) => r.operationName === "AccessOn")).toHaveLength(1);
          expect(state.success).not.toHaveBeenCalled();
          expect(state.warning).not.toHaveBeenCalled();
        } finally {
          hook.unmount();
          api.client.stop();
        }
      }
    );
  });
  it.each(["access-failed", "access-null"] as const)(
    "%s unavailable access is distinct from healthy empty with actual retry",
    async (mode) => {
      const api = context("fr", "binding", mode),
        t = tFor("fr"),
        view = render(<ActualAccess />, { wrapper: api.wrapper });
      try {
        await screen.findByText(
          mode === "access-failed" ? "RAW_ACCESS_READ_FAILURE" : t("unavailable")
        );
        expect(screen.queryByText(t("emptyTitle"))).not.toBeInTheDocument();
        api.setMode("ok");
        await userEvent.click(
          screen.getByRole("button", { name: tFor("fr", "shared.list")("retry") })
        );
        await screen.findByText("RAW_ROLE_NAME");
        expect(api.requests.filter((r) => r.operationName === "AccessOn")).toHaveLength(2);
      } finally {
        view.unmount();
        api.client.stop();
      }
    }
  );
  it("changed current target never exposes previousData or dispatches its old row", async () => {
    const api = context("de"),
      hook = renderHook(({ current }) => useEntityAccess(current, "LITERAL_SUBJECT"), {
        wrapper: api.wrapper,
        initialProps: { current: target as typeof target | null },
      });
    try {
      await waitFor(() => expect(hook.result.current.rows).toHaveLength(1));
      const old = hook.result.current.rows[0];
      api.setMode("target-wait");
      hook.rerender({ current: { kind: "TEAM", id: "new-team" } });
      await waitFor(() =>
        expect(
          api.requests.some(
            (r) => r.operationName === "AccessOn" && r.variables.scopeId === "new-team"
          )
        ).toBe(true)
      );
      expect(hook.result.current.rows).toEqual([]);
      expect(hook.result.current.loading).toBe(true);
      await act(async () => {
        await expect(hook.result.current.onRemove(old)).rejects.toThrow(tFor("de")("noTarget"));
      });
      expect(api.requests.some((r) => r.operationName === "RevokeRoleBinding")).toBe(false);
      api.release();
      await waitFor(() => expect(hook.result.current.loading).toBe(false));
      hook.rerender({ current: null });
      expect(hook.result.current.rows).toEqual([]);
      expect(hook.result.current.loading).toBe(true);
    } finally {
      api.release();
      hook.unmount();
      api.client.stop();
    }
  });
  it.each(["share", "future"] as const)(
    "%s actual current source never dispatches a fabricated role-binding mutation",
    async (source) => {
      const api = context("ko", source),
        hook = renderHook(() => useEntityAccess(target, "LITERAL_SUBJECT"), {
          wrapper: api.wrapper,
        });
      try {
        await waitFor(() => expect(hook.result.current.rows).toHaveLength(1));
        await act(async () => {
          await expect(hook.result.current.onRemove(hook.result.current.rows[0])).rejects.toThrow(
            tFor("ko")("notRemovable")
          );
        });
        expect(
          api.requests.some(
            (r) =>
              r.operationName === "RevokeRoleBinding" ||
              r.operationName === "DeleteGroupRoleMapping"
          )
        ).toBe(false);
      } finally {
        hook.unmount();
        api.client.stop();
      }
    }
  );

  it.each(["project-failed", "project-null"] as const)(
    "%s actual Team access route shows reach unavailable, raw names stay literal and real retry",
    async (mode) => {
      const api = context("es", "binding", mode),
        t = tFor("es", "teams.access"),
        view = render(<TeamAccessClient slug={TEAMS[0].slug} />, { wrapper: api.wrapper });
      try {
        await screen.findByText(
          mode === "project-failed" ? "RAW_PROJECT_READ_FAILURE" : t("unavailable")
        );
        expect(screen.queryByText(t("emptyTitle"))).not.toBeInTheDocument();
        api.setMode("ok");
        await userEvent.click(screen.getByRole("button", { name: t("retry") }));
        await screen.findByText(PROJECTS[0].name);
        expect(api.requests.filter((r) => r.operationName === "ListProjects")).toHaveLength(2);
        await screen.findByText("RAW_ROLE_NAME");
        expect(api.requests.find((r) => r.operationName === "AccessOn")?.variables.scopeId).toBe(
          TEAMS[0].id
        );
      } finally {
        view.unmount();
        api.client.stop();
      }
    }
  );
  it("actual cached reach failure is visible with real retry while project identity stays literal", async () => {
    const api = context("fr"),
      t = tFor("fr", "teams.access"),
      view = render(<TeamAccessClient slug={TEAMS[0].slug} />, { wrapper: api.wrapper });
    try {
      await screen.findByText(PROJECTS[0].name);
      api.setMode("project-failed");
      await act(async () => {
        await api.client.refetchQueries({ include: ["ListProjects"] }).catch(() => {});
      });
      await screen.findByText("RAW_PROJECT_READ_FAILURE");
      expect(screen.getByText(PROJECTS[0].name)).toBeInTheDocument();
      expect(
        screen.getByRole("link", { name: tFor("fr", "shared.list")("viewAll") })
      ).toHaveAttribute("href", `/administration/projects?q=${encodeURIComponent(TEAMS[0].slug)}`);
      api.setMode("ok");
      await userEvent.click(screen.getByRole("button", { name: t("retry") }));
      await waitFor(() =>
        expect(screen.queryByText("RAW_PROJECT_READ_FAILURE")).not.toBeInTheDocument()
      );
      expect(api.requests.filter((r) => r.operationName === "ListProjects")).toHaveLength(3);
    } finally {
      view.unmount();
      api.client.stop();
    }
  });
  it("cached project failure retains raw rows, shows failure without presenting a confirmed count", async () => {
    const api = context("pt-BR"),
      hook = renderHook(() => useTeamProjects(TEAMS[0].slug), { wrapper: api.wrapper });
    try {
      await waitFor(() => expect(hook.result.current.projects).toHaveLength(PROJECTS.length));
      api.setMode("project-failed");
      act(() => hook.result.current.onRetry());
      await waitFor(() =>
        expect(hook.result.current.error?.message).toBe("RAW_PROJECT_READ_FAILURE")
      );
      expect(hook.result.current.projects.map((p) => p.id)).toEqual(PROJECTS.map((p) => p.id));
    } finally {
      hook.unmount();
      api.client.stop();
    }
  });
});
function Fixture({
  rows = TEAM_ACCESS,
  canManage = true,
  onRemove = async () => {},
}: {
  rows?: AccessEntry[];
  canManage?: boolean;
  onRemove?: (entry: AccessEntry) => Promise<void>;
}) {
  const t = useTranslations("shared.access.entityPanel"),
    tt = useTranslations("teams.access"),
    list = useLocalListState(localizedEntityAccessList(t));
  return (
    <TeamAccessPanel
      slug={TEAMS[0].slug}
      access={{
        list,
        ...entityAccessProps({
          rows,
          canManage,
          onRemove,
          subject: tt("subject", { name: TEAMS[0].slug }),
        }),
      }}
      reach={{ projects: PROJECTS, loading: false, error: null, onRetry: () => {} }}
    />
  );
}
describe("Team access immutable metadata and request context", () => {
  it("locale change retains open removal target and raw role/principal identities", async () => {
    const wrap = (locale: string) => (
        <NextIntlClientProvider locale={locale} messages={catalogs[locale]} timeZone="UTC">
          <Fixture rows={[entries.binding]} />
        </NextIntlClientProvider>
      ),
      view = render(wrap("en"));
    try {
      await userEvent.click(
        screen.getByRole("button", {
          name: tFor("en", "shared.list")("rowActions", { label: tFor("en")("label") }),
        })
      );
      await userEvent.click(screen.getByRole("menuitem", { name: tFor("en")("removeGrant") }));
      view.rerender(wrap("ja"));
      expect(screen.getByRole("alertdialog")).toHaveTextContent(
        tFor("ja")("removeTitle", { role: "literal-role", name: entries.binding.user!.username })
      );
      expect(screen.getByRole("button", { name: tFor("ja")("remove") })).toBeEnabled();
      expect(screen.getByRole("alertdialog")).toHaveTextContent("acme");
    } finally {
      view.unmount();
    }
  });

  it.each(locales)(
    "%s SSR→hydrate preserves source/role/raw IDs and request timezone dates",
    async (locale) => {
      const errors = vi.fn(),
        t = tFor(locale),
        tt = tFor(locale, "teams.access"),
        node = (
          <NextIntlClientProvider
            locale={locale}
            messages={catalogs[locale]}
            timeZone="America/Costa_Rica"
            now={new Date("2026-09-30T12:00:00Z")}
          >
            <Fixture rows={[{ ...entries.mapping, expiresAt: "2026-06-03T00:30:00Z" }]} />
          </NextIntlClientProvider>
        ),
        container = document.createElement("div");
      container.innerHTML = renderToString(node);
      document.body.append(container);
      const before = container.querySelector("tbody")?.textContent;
      let root: ReturnType<typeof hydrateRoot> | undefined;
      try {
        expect(container.textContent).toContain(t("sourceLabels.GROUP_MAPPING"));
        expect(container.textContent).toContain(
          t("groupCount", { count: entries.mapping.groupMemberCount! })
        );
        expect(container.textContent).toContain("RAW_ROLE_NAME");
        expect(container.textContent).toContain(tt("reaches"));
        expect(container.textContent).toContain(
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
        expect(container.querySelector("tbody")?.textContent).toBe(before);
        expect(errors).not.toHaveBeenCalled();
      } finally {
        await act(async () => root?.unmount());
        container.remove();
      }
    }
  );
  it("unknown/future source and access identifiers remain literal, with no invented app-sharing association", async () => {
    const api = context("ja"),
      t = tFor("ja"),
      view = render(
        <Fixture
          rows={[
            {
              ...entries.binding,
              source: "__proto__",
              scopeKind: "FUTURE_KIND",
              sourceScopeLabel: "RAW_FUTURE_SCOPE",
            },
          ]}
        />,
        { wrapper: api.wrapper }
      );
    try {
      expect(screen.getByText("__proto__")).toBeInTheDocument();
      expect(screen.getByText("RAW_ROLE_NAME")).toBeInTheDocument();
      await userEvent.click(
        screen.getByRole("button", {
          name: tFor("ja", "shared.list")("rowActions", { label: t("label") }),
        })
      );
      expect(screen.getByRole("menuitem", { name: t("notRemovable") })).toHaveAttribute(
        "aria-disabled",
        "true"
      );
      expect(screen.queryByRole("menuitem", { name: t("appSharing") })).not.toBeInTheDocument();
      expect(api.intlErrors).not.toHaveBeenCalled();
    } finally {
      view.unmount();
      api.client.stop();
    }
  });
  it("known team share keeps a future technical access level literal", async () => {
    const api = context("fr"),
      t = tFor("fr"),
      share = { ...APP_ACCESS[APP_ACCESS.length - 1], accessLevel: "RAW_FUTURE_LEVEL" },
      view = render(<Fixture rows={[share]} />, { wrapper: api.wrapper });
    try {
      expect(screen.getByText(t("share", { level: "RAW_FUTURE_LEVEL" }))).toBeInTheDocument();
      await userEvent.click(
        screen.getByRole("button", {
          name: tFor("fr", "shared.list")("rowActions", { label: t("label") }),
        })
      );
      expect(screen.getByRole("menuitem", { name: t("appSharing") })).toHaveAttribute(
        "aria-disabled",
        "true"
      );
      expect(api.intlErrors).not.toHaveBeenCalled();
    } finally {
      view.unmount();
      api.client.stop();
    }
  });
  it("withdrawn management or a replaced current result blocks an open confirm without discarding identity", async () => {
    const api = context("de"),
      t = tFor("de"),
      remove = vi.fn(async () => {}),
      view = render(<Fixture rows={[entries.binding]} onRemove={remove} />, {
        wrapper: api.wrapper,
      });
    try {
      await userEvent.click(
        screen.getByRole("button", {
          name: tFor("de", "shared.list")("rowActions", { label: t("label") }),
        })
      );
      await userEvent.click(screen.getByRole("menuitem", { name: t("removeGrant") }));
      view.rerender(<Fixture rows={[entries.binding]} canManage={false} onRemove={remove} />);
      expect(screen.getByRole("button", { name: t("remove") })).toBeDisabled();
      view.rerender(<Fixture rows={[]} onRemove={remove} />);
      expect(screen.getByRole("button", { name: t("remove") })).toBeDisabled();
      expect(screen.getByRole("alertdialog")).toHaveTextContent("literal-role");
      expect(remove).not.toHaveBeenCalled();
    } finally {
      view.unmount();
      api.client.stop();
    }
  });
});
