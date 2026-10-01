import { readFileSync } from "node:fs";
import { parse, type MessageFormatElement } from "@formatjs/icu-messageformat-parser";
import userEvent from "@testing-library/user-event";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createTranslator, NextIntlClientProvider } from "next-intl";
import { hydrateRoot } from "react-dom/client";
import { renderToString } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useLocalListState, type ListState } from "@/components/list/use-list-state";
import { locales } from "@/i18n/config";
import { MembersScreen, type MembersScreenProps } from "./MembersScreen";
import {
  GROUPS,
  INVITATIONS,
  MEMBERS,
  ROLES,
  RESOLVED_INVITATIONS,
  membersProps,
} from "./members.fixtures";
import { localizedPeopleList, membersVariables, peopleList, PEOPLE_LIST } from "./people-model";

const access = vi.hoisted(() => ({ allowed: true, error: vi.fn() }));
vi.mock("sonner", () => ({ toast: { error: access.error } }));
vi.mock("@/lib/permissions/use-my-permissions", () => ({
  useMyPermissions: () => ({ can: () => access.allowed, loading: false, granted: new Set() }),
}));
const catalogs = Object.fromEntries(
  locales.map((locale) => [locale, JSON.parse(readFileSync(`messages/${locale}.json`, "utf8"))])
);
const now = new Date("2026-09-30T12:00:00Z");
const fixedMembers = [
  { ...MEMBERS[0], joinedAt: "2026-03-01T00:30:00Z", lastActiveAt: "2026-09-28T12:00:00Z" },
];
const tFor = (locale: string) =>
  createTranslator({ locale, messages: catalogs[locale], namespace: "orgMembers" });

function People({
  initial,
  ...over
}: Partial<Omit<MembersScreenProps, "list">> & { initial?: Partial<ListState> }) {
  const list = useLocalListState(PEOPLE_LIST, initial);
  return <MembersScreen {...membersProps(list, { members: fixedMembers })} {...over} list={list} />;
}
function provider(locale: string, children: React.ReactNode, errors = vi.fn()) {
  return (
    <NextIntlClientProvider
      locale={locale}
      messages={catalogs[locale]}
      now={now}
      timeZone="America/Los_Angeles"
      onError={errors}
    >
      {children}
    </NextIntlClientProvider>
  );
}
function leaves(value: Record<string, unknown>, prefix = ""): Record<string, string> {
  return Object.fromEntries(
    Object.entries(value).flatMap(([key, child]) => {
      const name = prefix ? `${prefix}.${key}` : key;
      return typeof child === "string"
        ? [[name, child]]
        : Object.entries(leaves(child as Record<string, unknown>, name));
    })
  );
}
function args(nodes: MessageFormatElement[]): string[] {
  return [
    ...new Set(
      nodes.flatMap((node): string[] => {
        if (node.type === 0 || node.type === 7) return [];
        if (node.type === 8) return [`tag:${node.value}`, ...args(node.children)];
        if (node.type === 5 || node.type === 6)
          return [
            `${node.type}:${node.value}`,
            ...Object.values(node.options).flatMap((option) => args(option.value)),
          ];
        return [`${node.type}:${node.value}`];
      })
    ),
  ].sort();
}
beforeEach(() => {
  localStorage.clear();
  access.allowed = true;
  access.error.mockClear();
});

describe("People presentation and current actions", () => {
  it.each(locales)(
    "%s has genuine complete ICU messages and preserves actual query/role identities",
    (locale) => {
      const canonical = leaves(catalogs.en.orgMembers.people),
        translated = leaves(catalogs[locale].orgMembers.people);
      expect(Object.keys(translated).sort()).toEqual(Object.keys(canonical).sort());
      const errors = vi.fn();
      const t = createTranslator({
        locale,
        messages: catalogs[locale],
        namespace: "orgMembers",
        onError: errors,
      });
      for (const [key, message] of Object.entries(canonical)) {
        expect(args(parse(translated[key]))).toEqual(args(parse(message)));
        expect(
          t(`people.${key}`, {
            count: 1234,
            name: "RAW_NAME/日本",
            email: "raw+exact@example.test",
          })
        ).not.toMatch(/\{(?:count|name|email)[,}]/);
      }
      if (locale !== "en") expect(translated.description).not.toBe(canonical.description);
      const raw = peopleList([{ ...ROLES[0], slug: "raw-role/日本", name: "Custom Role" }]);
      const localized = localizedPeopleList(raw, t);
      expect(localized.fields.find((field) => field.key === "role")?.options).toEqual(
        raw.fields.find((field) => field.key === "role")?.options
      );
      for (const field of raw.fields)
        expect(
          localized.fields
            .find((candidate) => candidate.key === field.key)
            ?.options?.map((option) => option.value)
        ).toEqual(field.options?.map((option) => option.value));
      for (const view of raw.views)
        expect(localized.views.find((candidate) => candidate.key === view.key)?.filters).toEqual(
          view.filters
        );
      const question = {
        q: "RAW_EMAIL",
        filters: { role: "raw-role/日本", team: "raw-team", lifecycle: "suspended", active: "7d" },
        sort: [{ key: "lastActive", dir: "desc" as const }],
        page: 7,
        pageSize: 50,
      };
      expect(membersVariables(question)).toMatchObject({
        search: "RAW_EMAIL",
        filter: {
          role: ["raw-role/日本"],
          team: ["raw-team"],
          lifecycle: ["suspended"],
          active: "7d",
        },
        sort: "-lastActive",
        page: 7,
        pageSize: 50,
      });
      expect(errors).not.toHaveBeenCalled();
    }
  );

  it.each(locales)(
    "%s uses request clock/timezone, literal identifiers and actual export callback",
    async (locale) => {
      const errors = vi.fn(),
        onExportCsv = vi.fn(),
        t = tFor(locale);
      render(provider(locale, <People totalCount={1234} onExportCsv={onExportCsv} />, errors));
      expect(screen.getByRole("heading", { name: t("people.title") })).toBeInTheDocument();
      expect(screen.getByText("ada@example.com")).toBeInTheDocument();
      expect(screen.getByText("org_owner")).toBeInTheDocument();
      expect(screen.getByText("platform")).toBeInTheDocument();
      expect(
        screen.getByText(t(`people.lifecycle.${fixedMembers[0].lifecycle}`))
      ).toBeInTheDocument();
      expect(
        screen.getByText(
          new Intl.DateTimeFormat(locale, {
            year: "numeric",
            month: "short",
            day: "numeric",
            timeZone: "America/Los_Angeles",
          }).format(new Date(fixedMembers[0].joinedAt))
        )
      ).toBeInTheDocument();
      expect(
        screen.getByText(new Intl.RelativeTimeFormat(locale).format(-2, "day"))
      ).toBeInTheDocument();
      const route = document.querySelector('a[href="/administration/access/people/m-1"]');
      expect(route).not.toBeNull();
      await userEvent.click(screen.getByRole("button", { name: t("people.export.actions") }));
      const item = screen.getByRole("menuitem", {
        name: t("people.export.label", { count: 1234 }),
      });
      expect(item).toBeEnabled();
      await userEvent.click(item);
      expect(onExportCsv).toHaveBeenCalledTimes(1);
      expect(errors).not.toHaveBeenCalled();
    }
  );

  it.each(locales)(
    "%s localizes group counts and keeps exact external group destinations",
    (locale) => {
      const errors = vi.fn(),
        t = tFor(locale);
      render(provider(locale, <People initial={{ view: "groups" }} />, errors));
      expect(
        screen.getAllByText(
          t("people.grants", { count: GROUPS[0].bindingsCount ?? 0 }) +
            ((GROUPS[0].mappingsCount ?? 0) > 0
              ? t("people.mappings", { count: GROUPS[0].mappingsCount! })
              : "")
        ).length
      ).toBeGreaterThan(0);
      expect(screen.getAllByText(GROUPS[0].groupExternalId!).length).toBeGreaterThan(0);
      expect(
        document.querySelector(
          `a[href="/administration/access/people/${encodeURIComponent("group:" + GROUPS[0].groupExternalId)}"]`
        )
      ).not.toBeNull();
      expect(
        screen.getByPlaceholderText(t("people.searchPlaceholder"), { normalizer: (text) => text })
      ).toBeInTheDocument();
      expect(
        screen.getByText(t("people.grants", { count: 1 }) + t("people.mappings", { count: 2 }))
      ).toBeInTheDocument();
      expect(errors).not.toHaveBeenCalled();
    }
  );

  it.each(locales)(
    "%s requires confirmation and preserves refusal and invitation identity",
    async (locale) => {
      const raw = { ...INVITATIONS[0], email: "raw+日本@example.test" };
      const revoke = vi.fn().mockRejectedValue(new Error("RAW_SERVER_REFUSAL"));
      const t = tFor(locale),
        errors = vi.fn();
      render(
        provider(
          locale,
          <People
            initial={{ view: "invited" }}
            rows={[{ kind: "invitation", key: `invitation:${raw.id}`, invitation: raw }]}
            onRevokeInvite={revoke}
          />,
          errors
        )
      );
      await userEvent.click(
        screen.getByRole("button", {
          name: createTranslator({ locale, messages: catalogs[locale], namespace: "shared.list" })(
            "rowActions",
            { label: t("people.title") }
          ),
        })
      );
      await userEvent.click(screen.getByRole("menuitem", { name: t("people.actions.revoke") }));
      expect(revoke).not.toHaveBeenCalled();
      expect(screen.getByRole("alertdialog")).toHaveTextContent(
        t("people.confirm.revoke.title", { email: raw.email })
      );
      await userEvent.click(screen.getByRole("button", { name: t("people.confirm.revoke.label") }));
      await waitFor(() =>
        expect(access.error).toHaveBeenCalledExactlyOnceWith("RAW_SERVER_REFUSAL")
      );
      expect(revoke).toHaveBeenCalledExactlyOnceWith(raw);
      expect(screen.getByRole("alertdialog")).toBeInTheDocument();
      expect(errors).not.toHaveBeenCalled();
    }
  );

  it.each(locales)(
    "%s does not offer denied manage actions and shows raw read errors",
    (locale) => {
      access.allowed = false;
      const t = tFor(locale),
        retry = vi.fn(),
        errors = vi.fn();
      render(
        provider(
          locale,
          <People
            canManageMembers={false}
            error={{ message: "RAW_READ_DENIAL" }}
            onRetry={retry}
          />,
          errors
        )
      );
      expect(
        screen.queryByRole("button", { name: t("people.actions.invite") })
      ).not.toBeInTheDocument();
      expect(
        screen.queryByRole("link", { name: t("people.actions.grant") })
      ).not.toBeInTheDocument();
      expect(screen.getByText("RAW_READ_DENIAL")).toBeInTheDocument();
      fireEvent.click(screen.getByRole("button", { name: catalogs[locale].shared.list.retry }));
      expect(retry).toHaveBeenCalledTimes(1);
      expect(errors).not.toHaveBeenCalled();
    }
  );

  it.each(locales)(
    "%s hydrates member dates and ages without replacing the request context",
    async (locale) => {
      const errors = vi.fn(),
        recoverable = vi.fn();
      const tree = provider(locale, <People />, errors),
        container = document.createElement("div");
      document.body.appendChild(container);
      container.innerHTML = renderToString(tree);
      const presentation = () => {
        const copy = container.cloneNode(true) as HTMLElement;
        copy.querySelectorAll('[data-slot="select-value"]').forEach((node) => node.remove());
        return copy.textContent;
      };
      const before = presentation();
      let root: ReturnType<typeof hydrateRoot> | undefined;
      try {
        await act(async () => {
          root = hydrateRoot(container, tree, { onRecoverableError: recoverable });
        });
        expect(presentation()).toBe(before);
        expect(errors).not.toHaveBeenCalled();
        expect(recoverable).not.toHaveBeenCalled();
      } finally {
        await act(async () => root?.unmount());
        container.remove();
      }
    }
  );

  it("preserves custom and future filter metadata and resolved invitation values", () => {
    const t = tFor("fr"),
      definition = peopleList(ROLES);
    definition.fields = [
      ...definition.fields,
      { key: "future", label: "RAW_FUTURE", options: [{ value: "RAW_VALUE", label: "RAW_LABEL" }] },
    ];
    const localized = localizedPeopleList(definition, t);
    expect(localized.fields.find((field) => field.key === "future")).toEqual(
      definition.fields.at(-1)
    );
    render(
      provider(
        "fr",
        <People
          initial={{ view: "invited", filters: { status: "accepted" } }}
          rows={[{ kind: "invitation", key: "resolved", invitation: RESOLVED_INVITATIONS[0] }]}
        />
      )
    );
    expect(screen.getByText(RESOLVED_INVITATIONS[0].email)).toBeInTheDocument();
  });
});
