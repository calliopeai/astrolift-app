import { readFileSync } from "node:fs";
import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { buildSchema, parse, validate } from "graphql";
import { beforeEach } from "vitest";
import {
  parse as parseMessage,
  type MessageFormatElement,
} from "@formatjs/icu-messageformat-parser";
import { hydrateRoot } from "react-dom/client";
import { renderToString } from "react-dom/server";
import { PeopleClient } from "@/app/(app)/administration/access/people/people-client";
import { MEMBERS } from "./members.fixtures";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createTranslator, NextIntlClientProvider } from "next-intl";
import { describe, expect, it, vi } from "vitest";
import { locales } from "@/i18n/config";
import { AnonymizeUserDialog } from "./AnonymizeUserDialog";

const catalogs = Object.fromEntries(
  locales.map((locale) => [locale, JSON.parse(readFileSync(`messages/${locale}.json`, "utf8"))])
);
function tFor(locale: string) {
  return createTranslator({
    locale,
    messages: catalogs[locale],
    namespace: "orgMembers.anonymization",
  });
}
function view(
  locale: string,
  targetId: string | null,
  name: string | null,
  confirm = async () => false,
  close = vi.fn()
) {
  return (
    <NextIntlClientProvider
      locale={locale}
      messages={catalogs[locale]}
      now={new Date("2026-09-30T12:00:00Z")}
      timeZone="America/Costa_Rica"
    >
      <AnonymizeUserDialog
        targetId={targetId}
        name={name}
        onConfirm={confirm}
        onOpenChange={close}
      />
    </NextIntlClientProvider>
  );
}
describe.each(locales)("Privacy review target in %s", (locale) => {
  it("requires a fresh acknowledgement for distinct exact GUIDs even when display names match", async () => {
    const result = render(view(locale, "USER_A", "LITERAL_NAME"));
    await userEvent.click(screen.getByRole("checkbox"));
    expect(screen.getByRole("button", { name: tFor(locale)("confirm") })).toBeEnabled();
    result.rerender(view(locale, "USER_B", "LITERAL_NAME"));
    expect(screen.getByRole("checkbox")).not.toBeChecked();
    expect(screen.getByRole("button", { name: tFor(locale)("confirm") })).toBeDisabled();
  });
  it("does not resurrect A's acknowledgement after A→B→A or confirmed source withdrawal", async () => {
    const result = render(view(locale, "USER_A", "LITERAL_NAME"));
    await userEvent.click(screen.getByRole("checkbox"));
    result.rerender(view(locale, "USER_B", "OTHER_NAME"));
    result.rerender(view(locale, "USER_A", "LITERAL_NAME"));
    expect(screen.getByRole("checkbox")).not.toBeChecked();
    await userEvent.click(screen.getByRole("checkbox"));
    result.rerender(view(locale, null, null));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    result.rerender(view(locale, "USER_A", "LITERAL_NAME"));
    expect(screen.getByRole("checkbox")).not.toBeChecked();
    expect(screen.getByRole("button", { name: tFor(locale)("confirm") })).toBeDisabled();
  });
  it("preserves acknowledgement through a language change for the same confirmed GUID/name", async () => {
    const result = render(view("en", "USER_A", "LITERAL_NAME"));
    await userEvent.click(screen.getByRole("checkbox"));
    result.rerender(view(locale, "USER_A", "LITERAL_NAME"));
    expect(screen.getByRole("checkbox")).toBeChecked();
    expect(screen.getByRole("button", { name: tFor(locale)("confirm") })).toBeEnabled();
    expect(screen.getByRole("alertdialog")).toHaveTextContent(
      tFor(locale)("title", { name: "LITERAL_NAME" })
    );
  });
  it("retains the existing irreversible review on refusal without closing it", async () => {
    const confirm = vi.fn(async () => false),
      close = vi.fn();
    render(view(locale, "USER_A", "LITERAL_NAME", confirm, close));
    await userEvent.click(screen.getByRole("checkbox"));
    await userEvent.click(screen.getByRole("button", { name: tFor(locale)("confirm") }));
    await waitFor(() => expect(confirm).toHaveBeenCalledTimes(1));
    expect(screen.getByRole("checkbox")).toBeChecked();
    expect(screen.getByRole("alertdialog")).toBeTruthy();
    expect(close).not.toHaveBeenCalled();
  });
});
it("a late accepted result cannot close a newer target review, including A→B→A", async () => {
  let finish!: (value: boolean) => void;
  const close = vi.fn(),
    confirm = vi.fn(
      () =>
        new Promise<boolean>((resolve) => {
          finish = resolve;
        })
    );
  const result = render(view("en", "USER_A", "LITERAL_NAME", confirm, close));
  await userEvent.click(screen.getByRole("checkbox"));
  await userEvent.click(screen.getByRole("button", { name: tFor("en")("confirm") }));
  await waitFor(() => expect(confirm).toHaveBeenCalledTimes(1));
  result.rerender(view("en", "USER_B", "OTHER_NAME", confirm, close));
  result.rerender(view("en", "USER_A", "LITERAL_NAME", confirm, close));
  await act(async () => finish(true));
  expect(screen.getByRole("alertdialog")).toBeTruthy();
  expect(screen.getByRole("checkbox")).not.toBeChecked();
  expect(close).not.toHaveBeenCalled();
});

const state = vi.hoisted(() => ({
  allowed: true,
  query: "",
  replace: vi.fn(),
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
}));
vi.mock("sonner", () => ({ toast: state }));
vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(state.query),
  usePathname: () => "/administration/access/people",
  useRouter: () => ({ replace: state.replace }),
}));
vi.mock("@/lib/permissions/use-my-permissions", () => ({
  useMyPermissions: () => ({ can: () => state.allowed, loading: false, granted: new Set() }),
}));
const schema = buildSchema(readFileSync("../backend/schema.graphql", "utf8"));
type Mode =
  | "ok"
  | "removed"
  | "read-failed"
  | "refused"
  | "refused-refresh-failed"
  | "refresh-failed"
  | "offline"
  | "logout"
  | "deferred";
function journey(
  locale: string,
  initialMode: Mode = "ok",
  initialFlag: boolean | null = false,
  inactiveLookalike = false
) {
  let mode = initialMode,
    anonymousFlag = initialFlag,
    accepted = false,
    release: (() => void) | undefined;
  const requests: { operationName: string; variables: Record<string, unknown> }[] = [];
  const record = (next = false) => ({
    ...MEMBERS[1],
    __typename: "AstroliftMember",
    id: next ? "MEMBER_NEXT" : "MEMBER_INITIAL",
    user: {
      __typename: "AstroliftUser",
      id: next ? "3" : "2",
      username: next
        ? "NEXT_LITERAL_NAME"
        : initialFlag === true || accepted
          ? "anon-2-TEST_ONLY"
          : "LITERAL_NAME",
      email: next
        ? "next@example.test"
        : initialFlag === true || accepted
          ? "anon-2-TEST_ONLY@anon-astrolift.net"
          : inactiveLookalike
            ? "literal@anon-astrolift.net"
            : "literal@example.test",
      isActive: next || !(initialFlag === true || accepted || inactiveLookalike),
      isAnonymized: next ? false : accepted ? true : anonymousFlag,
    },
    lifecycle:
      !next && (initialFlag === true || accepted || inactiveLookalike) ? "deactivated" : "active",
    isActive: next || !(initialFlag === true || accepted || inactiveLookalike),
    lastSeenAt: null,
    lastActiveAt: null,
    deletedAt: null,
    teamId: null,
    teamSlug: null,
    teamName: null,
    teams: [],
  });
  const client = new ApolloClient({
    cache: new InMemoryCache(),
    link: new HttpLink({
      uri: "https://privacy.test.invalid/graphql/",
      fetch: async (_url, options) => {
        const request = JSON.parse(String(options?.body));
        requests.push(request);
        expect(validate(schema, parse(request.query))).toEqual([]);
        if (request.operationName === "AnonymizeUser") {
          if (mode === "deferred")
            await new Promise<void>((resolve) => {
              release = resolve;
            });
          if (mode === "offline") throw new Error("RAW_TRANSPORT_FAILURE");
          const ok = !["refused", "refused-refresh-failed"].includes(mode);
          accepted = ok;
          return Response.json({
            data: {
              astroliftAnonymizeUser: {
                ok,
                errors: ok
                  ? []
                  : [{ code: "PERMISSION_DENIED", message: "RAW_SERVER_REFUSAL", field: null }],
                data: ok
                  ? {
                      __typename: "AstroliftAnonymizeUserPayload",
                      anonymizedUserId: "2",
                      wasSelf: mode === "logout",
                      requiresLogout: mode === "logout",
                      lifecycle: "deactivated",
                      anonymizedAt: "2026-09-30T12:00:00Z",
                    }
                  : null,
              },
            },
          });
        }
        if (request.operationName === "ListRoles")
          return Response.json({ data: { astroliftRoles: [] } });
        if (request.operationName === "ListRoleBindingsPage")
          return Response.json({
            data: {
              astroliftRoleBindingsPage: {
                __typename: "AstroliftRoleBindingPage",
                items: [],
                totalCount: 0,
                nextCursor: null,
                page: 1,
                pageSize: 200,
              },
            },
          });
        if (request.operationName === "ListMembersPage") {
          if (mode === "read-failed" || (accepted && mode === "refresh-failed"))
            throw new Error("RAW_READ_FAILURE");
          return Response.json({
            data: {
              astroliftMembersPage: {
                __typename: "AstroliftMemberPage",
                items: mode === "removed" ? [] : [record(request.variables.search === "next")],
                totalCount: mode === "removed" ? 0 : 1,
                nextCursor: null,
                page: 1,
                pageSize: 25,
              },
            },
          });
        }
        throw new Error("Unexpected operation: " + request.operationName);
      },
    }),
  });
  const result = render(
    <NextIntlClientProvider
      locale={locale}
      messages={catalogs[locale]}
      now={new Date("2026-09-30T12:00:00Z")}
      timeZone="America/Costa_Rica"
    >
      <ApolloProvider client={client}>
        <PeopleClient />
      </ApolloProvider>
    </NextIntlClientProvider>
  );
  return {
    ...result,
    client,
    requests,
    setMode: (next: Mode) => {
      mode = next;
    },
    setFlag: (flag: boolean | null) => {
      anonymousFlag = flag;
    },
    waiting: () => !!release,
    release: () => release?.(),
    rerenderRoute: () =>
      result.rerender(
        <NextIntlClientProvider
          locale={locale}
          messages={catalogs[locale]}
          now={new Date("2026-09-30T12:00:00Z")}
          timeZone="America/Costa_Rica"
        >
          <ApolloProvider client={client}>
            <PeopleClient />
          </ApolloProvider>
        </NextIntlClientProvider>
      ),
  };
}
async function openReview(locale: string, name = "LITERAL_NAME", anonymous = false) {
  await screen.findByText(name);
  const t = createTranslator({ locale, messages: catalogs[locale], namespace: "orgMembers" });
  const shared = createTranslator({ locale, messages: catalogs[locale], namespace: "shared.list" });
  await userEvent.click(
    screen.getByRole("button", { name: shared("rowActions", { label: t("people.title") }) })
  );
  await userEvent.click(
    screen.getByRole("menuitem", {
      name: t(anonymous ? "people.actions.reviewPrivacyCleanup" : "people.actions.anonymize"),
    })
  );
  expect(screen.getByRole("alertdialog")).toHaveTextContent(
    t(anonymous ? "anonymization.reviewTitle" : "anonymization.title", { name })
  );
}
beforeEach(() => {
  vi.clearAllMocks();
  state.replace.mockReset();
  localStorage.clear();
  state.allowed = true;
  state.query = "";
});
describe.each(locales)("Connected privacy review in %s", (locale) => {
  it("withdraws a confirmed current row and requires a new acknowledgement if it returns", async () => {
    const ctx = journey(locale);
    await openReview(locale);
    await userEvent.click(screen.getByRole("checkbox"));
    ctx.setMode("removed");
    await act(async () => {
      await ctx.client.refetchQueries({ include: ["ListMembersPage"] });
    });
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    ctx.setMode("ok");
    await act(async () => {
      await ctx.client.refetchQueries({ include: ["ListMembersPage"] });
    });
    await openReview(locale);
    expect(screen.getByRole("checkbox")).not.toBeChecked();
    expect(ctx.requests.some((r) => r.operationName === "AnonymizeUser")).toBe(false);
  });
  it("preserves the current cached target through failed refresh but withdraws existing permission admission", async () => {
    const ctx = journey(locale);
    await openReview(locale);
    await userEvent.click(screen.getByRole("checkbox"));
    ctx.setMode("read-failed");
    await act(async () => {
      await ctx.client.refetchQueries({ include: ["ListMembersPage"] }).catch(() => undefined);
    });
    expect(screen.getByRole("alertdialog")).toBeTruthy();
    expect(screen.getByRole("checkbox")).toBeChecked();
    state.allowed = false;
    ctx.rerenderRoute();
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    state.allowed = true;
    ctx.rerenderRoute();
    ctx.setMode("ok");
    await act(async () => {
      await ctx.client.refetchQueries({ include: ["ListMembersPage"] });
    });
    await openReview(locale);
    expect(screen.getByRole("checkbox")).not.toBeChecked();
    expect(ctx.requests.some((r) => r.operationName === "AnonymizeUser")).toBe(false);
  });
  it("a current filter change discards the prior target and its acknowledgement", async () => {
    const ctx = journey(locale);
    await openReview(locale);
    await userEvent.click(screen.getByRole("checkbox"));
    state.query = "q=next";
    ctx.rerenderRoute();
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    await openReview(locale, "NEXT_LITERAL_NAME");
    expect(screen.getByRole("checkbox")).not.toBeChecked();
    expect(ctx.requests.some((r) => r.operationName === "AnonymizeUser")).toBe(false);
  });
});

describe.each(locales)("Connected privacy outcomes in %s", (locale) => {
  it("retains an accepted mutation through failed refresh and retries the real current read", async () => {
    const ctx = journey(locale, "refresh-failed");
    await openReview(locale);
    const t = createTranslator({
      locale,
      messages: catalogs[locale],
      namespace: "orgMembers.feedback",
    });
    await userEvent.click(screen.getByRole("checkbox"));
    const button = screen.getByRole("button", { name: tFor(locale)("confirm") });
    button.focus();
    await userEvent.keyboard("{Enter}");
    await waitFor(() => expect(state.success).toHaveBeenCalledExactlyOnceWith(t("anonymized")));
    expect(state.warning).toHaveBeenCalledExactlyOnceWith(t("refreshWarning"));
    expect(state.error).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    expect(screen.getByText("RAW_READ_FAILURE")).toBeTruthy();
    ctx.setMode("ok");
    await userEvent.click(screen.getByRole("button", { name: catalogs[locale].shared.list.retry }));
    await waitFor(() => expect(screen.queryByText("RAW_READ_FAILURE")).toBeNull());
    expect(
      ctx.requests.filter((r) => r.operationName === "AnonymizeUser").map((r) => r.variables)
    ).toEqual([{ input: { userGid: "2" } }]);
    const reads = ctx.requests.filter((r) => r.operationName === "ListMembersPage");
    expect(reads).toHaveLength(3);
    expect(reads.map((r) => r.variables)).toEqual([
      reads[0].variables,
      reads[0].variables,
      reads[0].variables,
    ]);
    expect(state.replace).not.toHaveBeenCalled();
  });
  it.each(["refused", "refused-refresh-failed", "offline"] as const)(
    "%s keeps the actual review/refusal without accepted-write feedback or another read",
    async (mode) => {
      const ctx = journey(locale, mode);
      await openReview(locale);
      await userEvent.click(screen.getByRole("checkbox"));
      await userEvent.click(screen.getByRole("button", { name: tFor(locale)("confirm") }));
      await waitFor(() =>
        expect(state.error).toHaveBeenCalledExactlyOnceWith(
          mode === "offline" ? "RAW_TRANSPORT_FAILURE" : "RAW_SERVER_REFUSAL"
        )
      );
      expect(screen.getByRole("alertdialog")).toBeTruthy();
      expect(screen.getByRole("checkbox")).toBeChecked();
      expect(state.warning).not.toHaveBeenCalled();
      expect(state.success).not.toHaveBeenCalled();
      expect(state.replace).not.toHaveBeenCalled();
      expect(ctx.requests.filter((r) => r.operationName === "ListMembersPage")).toHaveLength(1);
      ctx.setMode("ok");
      await userEvent.click(screen.getByRole("button", { name: tFor(locale)("confirm") }));
      await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
      expect(
        ctx.requests.filter((r) => r.operationName === "AnonymizeUser").map((r) => r.variables)
      ).toEqual([{ input: { userGid: "2" } }, { input: { userGid: "2" } }]);
    }
  );
  it("honors only the actual backend logout signal after acceptance", async () => {
    const ctx = journey(locale, "logout");
    await openReview(locale);
    await userEvent.click(screen.getByRole("checkbox"));
    await userEvent.click(screen.getByRole("button", { name: tFor(locale)("confirm") }));
    await waitFor(() => expect(state.replace).toHaveBeenCalledExactlyOnceWith("/auth/logout"));
    expect(
      ctx.requests.filter((r) => r.operationName === "AnonymizeUser").map((r) => r.variables)
    ).toEqual([{ input: { userGid: "2" } }]);
  });
  it("Cancel closes without dispatching or reading again and a new review remains unchecked", async () => {
    const ctx = journey(locale);
    await openReview(locale);
    await userEvent.click(screen.getByRole("checkbox"));
    await userEvent.click(
      screen.getByRole("button", { name: catalogs[locale].shared.confirmation.cancel })
    );
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    await openReview(locale);
    expect(screen.getByRole("checkbox")).not.toBeChecked();
    expect(ctx.requests.some((r) => r.operationName === "AnonymizeUser")).toBe(false);
    expect(ctx.requests.filter((r) => r.operationName === "ListMembersPage")).toHaveLength(1);
  });
});
it("a late actual mutation reply for the prior filter/target cannot close the newer review", async () => {
  const ctx = journey("fr", "deferred");
  await openReview("fr");
  await userEvent.click(screen.getByRole("checkbox"));
  await userEvent.click(screen.getByRole("button", { name: tFor("fr")("confirm") }));
  await waitFor(() => expect(ctx.waiting()).toBe(true));
  state.query = "q=next";
  ctx.rerenderRoute();
  await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
  await openReview("fr", "NEXT_LITERAL_NAME");
  await act(async () => ctx.release());
  await waitFor(() => expect(state.success).toHaveBeenCalledTimes(1));
  expect(screen.getByRole("alertdialog")).toHaveTextContent(
    tFor("fr")("title", { name: "NEXT_LITERAL_NAME" })
  );
  expect(screen.getByRole("checkbox")).not.toBeChecked();
  expect(
    ctx.requests.filter((r) => r.operationName === "AnonymizeUser").map((r) => r.variables)
  ).toEqual([{ input: { userGid: "2" } }]);
});

describe.each(locales)("Authoritative privacy state in %s", (locale) => {
  it("uses only server true for a repeat cleanup and preserves exact identity/typed inputs without logout", async () => {
    const ctx = journey(locale, "ok", true);
    await openReview(locale, "anon-2-TEST_ONLY", true);
    const dialog = screen.getByRole("alertdialog"),
      t = tFor(locale);
    expect(dialog).toHaveTextContent(t("anonymousIdentity"));
    for (const key of [
      "history",
      "metadata",
      "records",
      "roleBindings",
      "scopeLimits",
      "external",
      "irreversible",
    ])
      expect(dialog).toHaveTextContent(t(key));
    for (const key of ["identity", "profile", "account", "memberships"])
      expect(dialog).not.toHaveTextContent(t(key));
    expect(screen.getByRole("button", { name: t("reviewConfirm") })).toBeDisabled();
    await userEvent.click(screen.getByRole("checkbox"));
    await userEvent.click(screen.getByRole("button", { name: t("reviewConfirm") }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    expect(await screen.findByText("anon-2-TEST_ONLY")).toBeTruthy();
    expect(screen.getByText("anon-2-TEST_ONLY@anon-astrolift.net")).toBeTruthy();
    expect(
      ctx.requests.filter((r) => r.operationName === "AnonymizeUser").map((r) => r.variables)
    ).toEqual([{ input: { userGid: "2" } }]);
    expect(state.replace).not.toHaveBeenCalled();
    await openReview(locale, "anon-2-TEST_ONLY", true);
    expect(screen.getByRole("checkbox")).not.toBeChecked();
  });
  it.each([false, null])(
    "a %s flag never infers anonymity from inactive state, deactivation or an anonymous-looking email",
    async (flag) => {
      journey(locale, "ok", flag, true);
      await openReview(locale);
      const dialog = screen.getByRole("alertdialog"),
        t = tFor(locale);
      expect(dialog).toHaveTextContent(t("identity"));
      expect(dialog).not.toHaveTextContent(t("anonymousIdentity"));
      expect(screen.queryByRole("button", { name: t("reviewConfirm") })).toBeNull();
      for (const key of [
        "history",
        "metadata",
        "records",
        "roleBindings",
        "scopeLimits",
        "external",
        "irreversible",
      ])
        expect(dialog).toHaveTextContent(t(key));
    }
  );
  it("withdrawal of the authoritative flag clears a prior repeat acknowledgement without inventing new permission", async () => {
    const ctx = journey(locale, "ok", true);
    await openReview(locale, "anon-2-TEST_ONLY", true);
    await userEvent.click(screen.getByRole("checkbox"));
    ctx.setFlag(null);
    await act(async () => {
      await ctx.client.refetchQueries({ include: ["ListMembersPage"] });
    });
    expect(screen.getByRole("alertdialog")).toHaveTextContent(
      tFor(locale)("title", { name: "anon-2-TEST_ONLY" })
    );
    expect(screen.getByRole("checkbox")).not.toBeChecked();
    expect(screen.getByRole("button", { name: tFor(locale)("confirm") })).toBeDisabled();
    expect(ctx.requests.some((r) => r.operationName === "AnonymizeUser")).toBe(false);
  });
  it("accepted self-cleanup stays accepted if actual logout navigation throws", async () => {
    const ctx = journey(locale, "logout");
    state.replace.mockImplementationOnce(() => {
      throw new Error("RAW_NAVIGATION_ERROR");
    });
    await openReview(locale);
    await userEvent.click(screen.getByRole("checkbox"));
    await userEvent.click(screen.getByRole("button", { name: tFor(locale)("confirm") }));
    const t = createTranslator({
      locale,
      messages: catalogs[locale],
      namespace: "orgMembers.feedback",
    });
    await waitFor(() => expect(state.success).toHaveBeenCalledExactlyOnceWith(t("anonymized")));
    expect(state.warning).toHaveBeenCalledExactlyOnceWith(t("logoutWarning"));
    expect(state.error).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    expect(
      ctx.requests.filter((r) => r.operationName === "AnonymizeUser").map((r) => r.variables)
    ).toEqual([{ input: { userGid: "2" } }]);
  });
});

function messageArguments(nodes: MessageFormatElement[]): string[] {
  return nodes
    .flatMap((node): string[] => {
      if (node.type === 0 || node.type === 7) return [];
      if (node.type === 8) return [`tag:${node.value}`, ...messageArguments(node.children)];
      if (node.type === 5 || node.type === 6)
        return [
          `${node.type}:${node.value}`,
          ...Object.values(node.options).flatMap((option) => messageArguments(option.value)),
        ];
      return [`${node.type}:${node.value}`];
    })
    .sort();
}
describe.each(locales)("Reviewed privacy copy and request context in %s", (locale) => {
  it("uses genuine translated privacy messages with matching ICU arguments", () => {
    for (const [key, english] of Object.entries(catalogs.en.orgMembers.anonymization)) {
      const translated = catalogs[locale].orgMembers.anonymization[key];
      expect(messageArguments(parseMessage(translated))).toEqual(
        messageArguments(parseMessage(english as string))
      );
      if (locale !== "en") expect(translated).not.toBe(english);
      expect(tFor(locale)(key, { name: "RAW_USER/日本" })).not.toContain("{name}");
    }
    for (const key of ["anonymized", "logoutWarning"]) {
      if (locale !== "en")
        expect(catalogs[locale].orgMembers.feedback[key]).not.toBe(
          catalogs.en.orgMembers.feedback[key]
        );
    }
    if (locale !== "en")
      expect(catalogs[locale].orgMembers.people.actions.reviewPrivacyCleanup).not.toBe(
        catalogs.en.orgMembers.people.actions.reviewPrivacyCleanup
      );
  });
  it("hydrates the closed server review then opens the same locale with authoritative repeat state", async () => {
    const container = document.createElement("div"),
      recoverable = vi.fn();
    document.body.appendChild(container);
    const tree = (name: string | null) => (
      <NextIntlClientProvider
        locale={locale}
        messages={catalogs[locale]}
        now={new Date("2026-09-30T12:00:00Z")}
        timeZone="America/Costa_Rica"
      >
        <AnonymizeUserDialog
          name={name}
          targetId="EXACT_GUID"
          isAnonymized={true}
          onConfirm={async () => false}
          onOpenChange={() => {}}
        />
      </NextIntlClientProvider>
    );
    container.innerHTML = renderToString(tree(null));
    let root: ReturnType<typeof hydrateRoot> | undefined;
    try {
      await act(async () => {
        root = hydrateRoot(container, tree(null), { onRecoverableError: recoverable });
      });
      expect(recoverable).not.toHaveBeenCalled();
      await act(async () => root?.render(tree("RAW_USER/日本")));
      expect(screen.getByRole("alertdialog")).toHaveTextContent(
        tFor(locale)("reviewTitle", { name: "RAW_USER/日本" })
      );
      expect(screen.getByRole("checkbox")).not.toBeChecked();
      expect(screen.getByRole("button", { name: tFor(locale)("reviewConfirm") })).toBeDisabled();
      expect(recoverable).not.toHaveBeenCalled();
    } finally {
      await act(async () => root?.unmount());
      container.remove();
    }
  });
});
