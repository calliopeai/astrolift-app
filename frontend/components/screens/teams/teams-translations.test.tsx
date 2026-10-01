import { readFileSync } from "node:fs";
import path from "node:path";
import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { parse, type MessageFormatElement } from "@formatjs/icu-messageformat-parser";
import { act, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { createTranslator, NextIntlClientProvider, useTranslations } from "next-intl";
import { useState, type ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { locales } from "@/i18n/config";
import { TEAMS } from "./teams.fixtures";
import { localizedTeamsList, TEAMS_LIST } from "./teams-list";
import { CreateTeamSheet } from "./CreateTeamSheet";
import { EditTeamSheet } from "./EditTeamSheet";
import { useCreateTeam } from "./use-create-team";
import { useEditTeam } from "./use-edit-team";
import { useTeams } from "./use-teams";
import { TeamsScreen } from "./TeamsScreen";
import { teamsScreenProps } from "./teams.fixtures";
import { useLocalListState } from "@/components/list/use-list-state";

const notifications = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  org: true,
  allowWrites: true,
}));
vi.mock("sonner", () => ({ toast: notifications }));
vi.mock("@/graphql/identity/identity.hooks", () => ({
  useActiveOrg: () => ({ org: notifications.org ? { id: "actual-org-id" } : null }),
}));
vi.mock("@/lib/permissions/use-my-permissions", () => ({
  useMyPermissions: () => ({ can: () => notifications.allowWrites, loading: false }),
}));
vi.mock("next/navigation", () => ({
  usePathname: () => "/administration/access/teams",
  useSearchParams: () => new URLSearchParams(),
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));
const catalogs = Object.fromEntries(
  locales.map((locale) => [
    locale,
    JSON.parse(readFileSync(path.resolve("messages", `${locale}.json`), "utf8")),
  ])
);
const tFor = (locale: string) =>
  createTranslator({ locale, messages: catalogs[locale], namespace: "teams" });
beforeEach(() => {
  notifications.org = true;
  notifications.allowWrites = true;
  notifications.success.mockClear();
  notifications.error.mockClear();
  notifications.warning.mockClear();
});
const team = TEAMS[0];
type Mode =
  | "ok"
  | "refused"
  | "no-diagnostic"
  | "refresh-failed"
  | "transport-failed"
  | "refused-refresh-failed";
type Request = { operationName: string; variables: Record<string, unknown> };

function clientFor(locale: string, mode: Mode) {
  const requests: Request[] = [];
  const counts = new Map<string, number>();
  const fetcher = vi.fn(async (_url: unknown, init?: RequestInit) => {
    const request = JSON.parse(String(init?.body)) as Request;
    requests.push(request);
    counts.set(request.operationName, (counts.get(request.operationName) ?? 0) + 1);
    let data;
    if (request.operationName === "ListTeams") {
      if (["refresh-failed", "refused-refresh-failed"].includes(mode))
        throw new Error("ACTUAL_REFRESH_UNAVAILABLE");
      data = { astroliftTeams: [team] };
    } else if (request.operationName === "ListTeamsPage") {
      if (
        ["refresh-failed", "refused-refresh-failed"].includes(mode) &&
        counts.get(request.operationName)! > 1
      )
        throw new Error("ACTUAL_REFRESH_UNAVAILABLE");
      data = { astroliftTeamsPage: { items: [team], totalCount: 1, nextCursor: null } };
    } else if (request.operationName === "TeamSlugAvailable") {
      data = { astroliftTeamSlugAvailable: true };
    } else {
      if (mode === "transport-failed") throw new Error("RAW_TRANSPORT_FAILURE");
      const field = (
        {
          CreateTeam: "createTeam",
          UpdateTeam: "updateTeam",
          SoftDeleteTeam: "softDeleteTeam",
        } as Record<string, string>
      )[request.operationName];
      if (!field) throw new Error(`Unexpected operation ${request.operationName}`);
      const refused =
        mode === "refused" || mode === "no-diagnostic" || mode === "refused-refresh-failed";
      const input = request.variables.input as Record<string, string>;
      data = {
        [field]: {
          ok: !refused,
          errors: ["refused", "refused-refresh-failed"].includes(mode)
            ? [{ code: "DENIED", message: "RAW_POLICY_REFUSAL", field: null }]
            : [],
          data: refused
            ? null
            : field === "softDeleteTeam"
              ? { id: team.id, deleted: true }
              : {
                  ...team,
                  name: input.name,
                  slug: input.slug,
                  organization: {
                    ...team.organization,
                    id: input.organizationId ?? team.organization.id,
                  },
                },
        },
      };
    }
    return new Response(JSON.stringify({ data }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  });
  const client = new ApolloClient({
    cache: new InMemoryCache(),
    link: new HttpLink({ uri: "https://example.invalid/graphql", fetch: fetcher as typeof fetch }),
  });
  const errors = vi.fn();
  const wrapper = ({ children }: { children: ReactNode }) => (
    <NextIntlClientProvider
      locale={locale}
      messages={catalogs[locale]}
      now={new Date("2026-09-30T12:00:00Z")}
      timeZone="America/Los_Angeles"
      onError={errors}
    >
      <ApolloProvider client={client}>{children}</ApolloProvider>
    </NextIntlClientProvider>
  );
  return { client, requests, wrapper, errors };
}
function Create() {
  const [open, setOpen] = useState(true);
  return (
    <>
      <CreateTeamSheet {...useCreateTeam()} open={open} onOpenChange={setOpen} />
      <output data-testid="open">{String(open)}</output>
    </>
  );
}
function Edit() {
  const [open, setOpen] = useState(true);
  return (
    <>
      <EditTeamSheet
        {...useEditTeam({ open, team })}
        open={open}
        team={team}
        onOpenChange={setOpen}
      />
      <output data-testid="open">{String(open)}</output>
    </>
  );
}

function ReadOnlyList() {
  const t = useTranslations("teams");
  const list = useLocalListState(localizedTeamsList(t));
  return (
    <TeamsScreen
      {...teamsScreenProps([{ ...team, createdAt: "2026-09-30T00:30:00Z" }])}
      list={list}
      canUpdate={false}
      canDelete={false}
      renderCreateDialog={() => null}
      renderEditDialog={() => null}
    />
  );
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
function argumentsOf(nodes: MessageFormatElement[]): string[] {
  return [
    ...new Set(
      nodes.flatMap((node): string[] => {
        if (node.type === 0 || node.type === 7) return [];
        if (node.type === 8) return [`tag:${node.value}`, ...argumentsOf(node.children)];
        if (node.type === 5 || node.type === 6)
          return [
            `${node.type}:${node.value}`,
            ...Object.values(node.options).flatMap((option) => argumentsOf(option.value)),
          ];
        return [`${node.type}:${node.value}`];
      })
    ),
  ].sort();
}

describe("Teams translations and connected outcomes", () => {
  it.each(locales)(
    "%s keeps dates in the request time zone and read-only decisions intact",
    (locale) => {
      notifications.allowWrites = false;
      const errors = vi.fn();
      render(
        <NextIntlClientProvider
          locale={locale}
          messages={catalogs[locale]}
          timeZone="America/Los_Angeles"
          onError={errors}
        >
          <ReadOnlyList />
        </NextIntlClientProvider>
      );
      expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(tFor(locale)("title"));
      const date = new Intl.DateTimeFormat(locale, {
        year: "numeric",
        month: "short",
        day: "numeric",
        timeZone: "America/Los_Angeles",
      }).format(new Date("2026-09-30T00:30:00Z"));
      expect(screen.getByText(date.replace(/\s+/g, " "))).toBeInTheDocument();
      expect(screen.getByText(team.name)).toBeInTheDocument();
      expect(screen.getByText(team.slug)).toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: tFor(locale)("newTeam") })
      ).not.toBeInTheDocument();
      expect(errors).not.toHaveBeenCalled();
    }
  );

  it("does not submit without an active organization or with an invalid slug", () => {
    notifications.org = false;
    const context = clientFor("ja", "ok"),
      view = render(<Create />, { wrapper: context.wrapper }),
      t = tFor("ja");
    try {
      fireEvent.change(screen.getByLabelText(t("displayName")), {
        target: { value: "ACTUAL_NAME" },
      });
      fireEvent.change(screen.getByLabelText(t("slug")), { target: { value: "valid-slug" } });
      expect(screen.getByRole("button", { name: t("create") })).toBeDisabled();
      fireEvent.click(screen.getByRole("button", { name: t("create") }));
      notifications.org = true;
      fireEvent.change(screen.getByLabelText(t("slug")), { target: { value: "-invalid" } });
      expect(screen.getByRole("button", { name: t("create") })).toBeDisabled();
      fireEvent.click(screen.getByRole("button", { name: t("create") }));
      expect(context.requests).toEqual([]);
      expect(notifications.success).not.toHaveBeenCalled();
    } finally {
      view.unmount();
      context.client.stop();
    }
  });
  it.each(locales)("%s preserves ICU contracts and actual list identities", (locale) => {
    const english = flatten(catalogs.en.teams),
      translated = flatten(catalogs[locale].teams);
    expect(Object.keys(translated).sort()).toEqual(Object.keys(english).sort());
    for (const [key, value] of Object.entries(english))
      expect(argumentsOf(parse(translated[key]))).toEqual(argumentsOf(parse(value)));
    const list = localizedTeamsList(tFor(locale));
    expect(list.views.map(({ key, filters }) => ({ key, filters }))).toEqual(
      TEAMS_LIST.views.map(({ key, filters }) => ({ key, filters }))
    );
    expect(list).toMatchObject({
      id: TEAMS_LIST.id,
      defaultSort: TEAMS_LIST.defaultSort,
      paging: TEAMS_LIST.paging,
      pageSizes: TEAMS_LIST.pageSizes,
      fields: [],
    });
  });

  describe.each([
    "ok",
    "refused",
    "no-diagnostic",
    "refresh-failed",
    "transport-failed",
    "refused-refresh-failed",
  ] as const)("%s", (mode) => {
    it.each(locales)(
      "%s submits exact create values and retains only failed drafts",
      async (locale) => {
        const context = clientFor(locale, mode),
          t = tFor(locale),
          view = render(<Create />, { wrapper: context.wrapper });
        try {
          fireEvent.change(screen.getByLabelText(t("displayName")), {
            target: { value: " EXACT_USER_NAME " },
          });
          fireEvent.change(screen.getByLabelText(t("slug")), { target: { value: "exact-slug" } });
          fireEvent.change(screen.getByLabelText(t("descriptionOptional")), {
            target: { value: " RAW_DESCRIPTION " },
          });
          fireEvent.click(screen.getByRole("button", { name: t("create") }));
          const committed = mode === "ok" || mode === "refresh-failed";
          await waitFor(() =>
            expect(committed ? notifications.success : notifications.error).toHaveBeenCalledOnce()
          );
          expect(
            context.requests
              .filter((request) => request.operationName === "CreateTeam")
              .map((request) => request.variables)
          ).toEqual([
            {
              input: {
                organizationId: "actual-org-id",
                name: "EXACT_USER_NAME",
                slug: "exact-slug",
                description: "RAW_DESCRIPTION",
              },
            },
          ]);
          expect(screen.getByTestId("open")).toHaveTextContent(String(!committed));
          if (!committed) {
            expect(screen.getByLabelText(t("displayName"))).toHaveValue(" EXACT_USER_NAME ");
            expect(notifications.error).toHaveBeenCalledWith(
              ["refused", "refused-refresh-failed"].includes(mode)
                ? "RAW_POLICY_REFUSAL"
                : mode === "transport-failed"
                  ? "RAW_TRANSPORT_FAILURE"
                  : t("feedback.createFailed")
            );
          } else {
            expect(notifications.success).toHaveBeenCalledWith(
              t("feedback.created", { slug: "exact-slug" })
            );
            expect(notifications.error).not.toHaveBeenCalled();
          }
          if (mode === "refresh-failed")
            expect(notifications.warning).toHaveBeenCalledWith(t("feedback.refreshWarning"));
          if (mode === "refused-refresh-failed") {
            expect(notifications.warning).not.toHaveBeenCalled();
            expect(context.requests.filter((r) => r.operationName === "ListTeams")).toEqual([]);
          }
          expect(context.errors).not.toHaveBeenCalled();
        } finally {
          view.unmount();
          context.client.stop();
        }
      }
    );

    it.each(locales)(
      "%s saves the actual team and preserves refused edit drafts",
      async (locale) => {
        const context = clientFor(locale, mode),
          t = tFor(locale),
          view = render(<Edit />, { wrapper: context.wrapper });
        try {
          fireEvent.change(screen.getByLabelText(t("displayName")), {
            target: { value: " EXACT_EDIT " },
          });
          fireEvent.click(screen.getByRole("button", { name: t("save") }));
          const committed = mode === "ok" || mode === "refresh-failed";
          await waitFor(() =>
            expect(committed ? notifications.success : notifications.error).toHaveBeenCalledOnce()
          );
          expect(
            context.requests
              .filter((request) => request.operationName === "UpdateTeam")
              .map((request) => request.variables)
          ).toEqual([{ input: { id: team.id, name: "EXACT_EDIT", slug: team.slug } }]);
          expect(screen.getByTestId("open")).toHaveTextContent(String(!committed));
          if (!committed)
            expect(screen.getByLabelText(t("displayName"))).toHaveValue(" EXACT_EDIT ");
          if (mode === "refresh-failed") {
            expect(notifications.warning).toHaveBeenCalledWith(t("feedback.refreshWarning"));
            expect(notifications.error).not.toHaveBeenCalled();
          }
          if (mode === "refused-refresh-failed") {
            expect(notifications.warning).not.toHaveBeenCalled();
            expect(context.requests.filter((r) => r.operationName === "ListTeams")).toEqual([]);
          }
          expect(context.errors).not.toHaveBeenCalled();
        } finally {
          view.unmount();
          context.client.stop();
        }
      }
    );
  });

  it.each(locales)(
    "%s keeps accepted deletion after failed refresh with exact target and page variables",
    async (locale) => {
      const context = clientFor(locale, "refresh-failed");
      const hook = renderHook(() => useTeams(), { wrapper: context.wrapper });
      try {
        await waitFor(() => expect(hook.result.current.rows).toHaveLength(1));
        await act(async () => hook.result.current.onDelete(team));
        expect(
          context.requests
            .filter((request) => request.operationName === "SoftDeleteTeam")
            .map((request) => request.variables)
        ).toEqual([{ input: { id: team.id } }]);
        expect(notifications.success).toHaveBeenCalledExactlyOnceWith(
          tFor(locale)("feedback.deleted", { slug: team.slug })
        );
        expect(notifications.warning).toHaveBeenCalledWith(tFor(locale)("feedback.refreshWarning"));
        expect(notifications.error).not.toHaveBeenCalled();
        expect(context.errors).not.toHaveBeenCalled();
      } finally {
        hook.unmount();
        context.client.stop();
      }
    }
  );

  it.each(locales)(
    "%s refuses actual deletion with its original diagnostic and no success",
    async (locale) => {
      const context = clientFor(locale, "refused"),
        hook = renderHook(() => useTeams(), { wrapper: context.wrapper });
      try {
        await waitFor(() => expect(hook.result.current.rows).toHaveLength(1));
        await act(async () => {
          await expect(hook.result.current.onDelete(team)).rejects.toThrow("RAW_POLICY_REFUSAL");
        });
        expect(
          context.requests.filter((request) => request.operationName === "SoftDeleteTeam")
        ).toHaveLength(1);
        expect(notifications.success).not.toHaveBeenCalled();
        expect(hook.result.current.rows[0].id).toBe(team.id);
      } finally {
        hook.unmount();
        context.client.stop();
      }
    }
  );
  it.each(locales)(
    "%s rejected retirement never refreshes an unavailable read or reports success",
    async (locale) => {
      const context = clientFor(locale, "refused-refresh-failed"),
        hook = renderHook(() => useTeams(), { wrapper: context.wrapper });
      try {
        await waitFor(() => expect(hook.result.current.rows).toHaveLength(1));
        await act(async () => {
          await expect(hook.result.current.onDelete(team)).rejects.toThrow("RAW_POLICY_REFUSAL");
        });
        expect(notifications.success).not.toHaveBeenCalled();
        expect(notifications.warning).not.toHaveBeenCalled();
        expect(context.requests.filter((r) => r.operationName === "ListTeams")).toEqual([]);
        expect(context.requests.filter((r) => r.operationName === "ListTeamsPage")).toHaveLength(1);
        expect(hook.result.current.rows[0].id).toBe(team.id);
      } finally {
        hook.unmount();
        context.client.stop();
      }
    }
  );
});
