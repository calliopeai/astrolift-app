import { readFileSync } from "node:fs";
import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { parse as parseMessage } from "@formatjs/icu-messageformat-parser";
import { act, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { buildSchema, parse, validate } from "graphql";
import { createTranslator, NextIntlClientProvider } from "next-intl";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { locales } from "@/i18n/config";
import { AnonymizeUserDialog } from "./AnonymizeUserDialog";
import { INVITATIONS } from "./members.fixtures";
import { useMembers } from "./use-members";

const state = vi.hoisted(() => ({
  query: "",
  replace: vi.fn(),
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
}));
vi.mock("sonner", () => ({
  toast: { success: state.success, error: state.error, warning: state.warning },
}));
vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(state.query),
  usePathname: () => "/administration/access/people",
  useRouter: () => ({ replace: state.replace }),
}));
vi.mock("@/lib/permissions/use-my-permissions", () => ({
  useMyPermissions: () => ({ can: () => true }),
}));
const schema = buildSchema(readFileSync("../backend/schema.graphql", "utf8"));
const catalogs = Object.fromEntries(
  locales.map((locale) => [locale, JSON.parse(readFileSync(`messages/${locale}.json`, "utf8"))])
);
const inv = INVITATIONS[0];
const operations = ["revoke", "delete", "resend", "anonymize"] as const;
type Action = (typeof operations)[number];
type Mode =
  | "ok"
  | "refused"
  | "no-message"
  | "missing-result"
  | "offline"
  | "refresh-failed"
  | "refused-refresh-failed"
  | "logout";
const names = {
  revoke: "RevokeInvitation",
  delete: "DeleteInvitation",
  resend: "ResendInvitation",
  anonymize: "AnonymizeUser",
};
const fields = {
  revoke: "revokeInvitation",
  delete: "deleteInvitation",
  resend: "resendInvitation",
  anonymize: "astroliftAnonymizeUser",
};
const failedKeys = {
  revoke: "revokeFailed",
  delete: "deleteFailed",
  resend: "resendFailed",
  anonymize: "anonymizeFailed",
};
const successKeys = {
  revoke: "revoked",
  delete: "deleted",
  resend: "resent",
  anonymize: "anonymized",
};
const tFor = (locale: string) =>
  createTranslator({ locale, messages: catalogs[locale], namespace: "orgMembers.feedback" });
function provider(locale: string, children: React.ReactNode, errors = vi.fn()) {
  return (
    <NextIntlClientProvider
      locale={locale}
      messages={catalogs[locale]}
      timeZone="UTC"
      now={new Date("2026-09-30T12:00:00Z")}
      onError={errors}
    >
      {children}
    </NextIntlClientProvider>
  );
}
function consumer(locale: string, action: Action, mode: Mode) {
  state.query = action === "anonymize" ? "" : "view=invited";
  let mutated = false;
  const requests: { operationName: string; variables: Record<string, unknown> }[] = [];
  const errors = vi.fn();
  const client = new ApolloClient({
    cache: new InMemoryCache(),
    link: new HttpLink({
      uri: "https://members.test.invalid/graphql/",
      fetch: async (_uri, options) => {
        const request = JSON.parse(String(options?.body));
        requests.push(request);
        expect(validate(schema, parse(request.query))).toEqual([]);
        if (request.operationName === names[action]) {
          expect(request.variables).toEqual({
            input: action === "anonymize" ? { userGid: "2" } : { id: inv.id },
          });
          mutated = true;
          if (mode === "missing-result") return Response.json({ data: { [fields[action]]: null } });
          if (mode === "offline") throw new Error("ORIGINAL_TRANSPORT_FAILURE");
          const refused = ["refused", "no-message", "refused-refresh-failed"].includes(mode);
          const payload =
            action === "anonymize"
              ? {
                  __typename: "AstroliftAnonymizeUserPayload",
                  anonymizedUserId: "2",
                  wasSelf: mode === "logout",
                  requiresLogout: mode === "logout",
                  lifecycle: "deactivated",
                  anonymizedAt: "2026-09-30T12:00:00Z",
                }
              : action === "resend"
                ? {
                    __typename: "AstroliftInvitationCreated",
                    invitation: { ...inv, __typename: "AstroliftInvitation" },
                    plaintextToken: "test-only-token",
                    acceptUrlPath: "/accept/test-only-link",
                  }
                : {
                    ...inv,
                    __typename: "AstroliftInvitation",
                    status: action === "revoke" ? "revoked" : inv.status,
                  };
          return Response.json({
            data: {
              [fields[action]]: {
                ok: !refused,
                errors:
                  refused && mode !== "no-message"
                    ? [{ code: "DENIED", message: "RAW_SERVER_REFUSAL" }]
                    : [],
                data: refused ? null : payload,
              },
            },
          });
        }
        if (mutated && ["refresh-failed", "refused-refresh-failed"].includes(mode))
          throw new Error("ORIGINAL_REFRESH_FAILURE");
        if (request.operationName === "ListRoles")
          return Response.json({ data: { astroliftRoles: [] } });
        expect(["ListMembersPage", "ListInvitationsPage"]).toContain(request.operationName);
        const members = request.operationName === "ListMembersPage";
        return Response.json({
          data: {
            [members ? "astroliftMembersPage" : "astroliftInvitationsPage"]: {
              __typename: members ? "AstroliftMemberPage" : "AstroliftInvitationPage",
              items: [],
              totalCount: 0,
              nextCursor: null,
              page: 1,
              pageSize: 25,
            },
          },
        });
      },
    }),
  });
  const hook = renderHook(() => useMembers(), {
    wrapper: ({ children }) =>
      provider(locale, <ApolloProvider client={client}>{children}</ApolloProvider>, errors),
  });
  return {
    ...hook,
    requests,
    errors,
    invoke: () =>
      action === "anonymize"
        ? hook.result.current.onAnonymize("2")
        : action === "revoke"
          ? hook.result.current.onRevokeInvite(inv)
          : action === "delete"
            ? hook.result.current.onDeleteInvite(inv)
            : hook.result.current.onResendInvite(inv),
    stop: () => {
      hook.unmount();
      client.stop();
    },
  };
}
beforeEach(() => {
  localStorage.clear();
  vi.clearAllMocks();
});

describe("connected People mutations and honest anonymization", () => {
  it.each(locales)("%s retains complete feedback/dialog ICU contracts", (locale) => {
    const errors = vi.fn();
    for (const namespace of ["feedback", "anonymization"] as const) {
      const canonical = catalogs.en.orgMembers[namespace],
        translated = catalogs[locale].orgMembers[namespace];
      expect(Object.keys(translated).sort()).toEqual(Object.keys(canonical).sort());
      const t = createTranslator({
        locale,
        messages: catalogs[locale],
        namespace: `orgMembers.${namespace}`,
        onError: errors,
      });
      for (const [key, message] of Object.entries(canonical)) {
        expect(parseMessage(translated[key]).filter((node) => node.type !== 0)).toEqual(
          parseMessage(message as string).filter((node) => node.type !== 0)
        );
        expect(t(key, { name: "RAW_NAME", email: "raw+exact@example.test" })).not.toMatch(
          /\{(?:name|email)[,}]/
        );
      }
    }
    expect(errors).not.toHaveBeenCalled();
  });
  it.each(locales)(
    "%s keeps committed outcomes through failed list refreshes for every mutation",
    async (locale) => {
      for (const action of operations) {
        vi.clearAllMocks();
        const view = consumer(locale, action, "refresh-failed"),
          t = tFor(locale);
        try {
          await waitFor(() => expect(view.result.current.loading).toBe(false));
          let outcome: unknown;
          await act(async () => {
            outcome = await view.invoke();
          });
          expect(outcome).toBe(action === "anonymize" ? true : undefined);
          expect(state.success).toHaveBeenCalledWith(
            t(successKeys[action], { email: inv.email }),
            ...(action === "resend" ? [expect.any(Object)] : [])
          );
          expect(state.warning).toHaveBeenCalledExactlyOnceWith(t("refreshWarning"));
          expect(state.error).not.toHaveBeenCalled();
          expect(state.replace).not.toHaveBeenCalled();
          expect(
            view.requests.filter((request) => request.operationName === names[action])
          ).toHaveLength(1);
          expect(view.errors).not.toHaveBeenCalled();
        } finally {
          view.stop();
        }
      }
    }
  );

  it.each(locales)(
    "%s preserves every raw/fallback/transport refusal without a committed warning",
    async (locale) => {
      for (const action of operations)
        for (const mode of [
          "refused",
          "no-message",
          "missing-result",
          "offline",
          "refused-refresh-failed",
        ] as const) {
          vi.clearAllMocks();
          const view = consumer(locale, action, mode),
            t = tFor(locale);
          try {
            await waitFor(() => expect(view.result.current.loading).toBe(false));
            const expected =
              mode === "no-message" || mode === "missing-result"
                ? t(failedKeys[action])
                : mode === "offline"
                  ? "ORIGINAL_TRANSPORT_FAILURE"
                  : "RAW_SERVER_REFUSAL";
            let caught: unknown, outcome: unknown;
            await act(async () => {
              try {
                outcome = await view.invoke();
              } catch (error) {
                caught = error;
              }
            });
            if (action === "anonymize") {
              expect(outcome).toBe(false);
              expect(state.error).toHaveBeenCalledExactlyOnceWith(expected);
            } else expect(caught).toEqual(new Error(expected));
            expect(state.success).not.toHaveBeenCalled();
            expect(state.warning).not.toHaveBeenCalled();
            expect(state.replace).not.toHaveBeenCalled();
            expect(view.requests).toHaveLength(3);
            expect(view.errors).not.toHaveBeenCalled();
          } finally {
            view.stop();
          }
        }
    }
  );

  it.each(locales)("%s honors the actual self-anonymization logout signal", async (locale) => {
    const view = consumer(locale, "anonymize", "logout");
    try {
      await waitFor(() => expect(view.result.current.loading).toBe(false));
      await act(async () => {
        expect(await view.invoke()).toBe(true);
      });
      expect(state.replace).toHaveBeenCalledExactlyOnceWith("/auth/logout");
    } finally {
      view.stop();
    }
  });

  it.each(locales)(
    "%s waits for actual clipboard completion and handles denial or missing support",
    async (locale) => {
      const view = consumer(locale, "resend", "ok"),
        t = tFor(locale);
      const original = Object.getOwnPropertyDescriptor(navigator, "clipboard");
      try {
        await waitFor(() => expect(view.result.current.loading).toBe(false));
        await act(async () => {
          await view.invoke();
        });
        const action = state.success.mock.calls[0][1].action;
        expect(action.label).toBe(t("copyLink"));
        let finish!: () => void;
        const copy = vi.fn(
          () =>
            new Promise<void>((resolve) => {
              finish = resolve;
            })
        );
        Object.defineProperty(navigator, "clipboard", {
          configurable: true,
          value: { writeText: copy },
        });
        const pending = action.onClick();
        expect(state.success).toHaveBeenCalledTimes(1);
        expect(copy).toHaveBeenCalledExactlyOnceWith(
          `${window.location.origin}/accept/test-only-link`
        );
        finish();
        await pending;
        expect(state.success).toHaveBeenLastCalledWith(t("linkCopied"));
        copy.mockRejectedValueOnce(new Error("DENIED_CLIPBOARD"));
        await action.onClick();
        expect(state.error).toHaveBeenLastCalledWith(t("copyFailed"));
        Object.defineProperty(navigator, "clipboard", { configurable: true, value: undefined });
        await action.onClick();
        expect(state.error).toHaveBeenLastCalledWith(t("copyFailed"));
        expect(state.success).toHaveBeenCalledTimes(2);
      } finally {
        view.stop();
        if (original) Object.defineProperty(navigator, "clipboard", original);
        else Reflect.deleteProperty(navigator, "clipboard");
      }
    }
  );

  it.each(locales)(
    "%s requires target-specific acknowledgement and preserves the failed dialog for retry",
    async (locale) => {
      const errors = vi.fn(),
        confirm = vi.fn().mockResolvedValue(false),
        close = vi.fn();
      const t = createTranslator({
        locale,
        messages: catalogs[locale],
        namespace: "orgMembers.anonymization",
      });
      const view = render(
        provider(
          locale,
          <AnonymizeUserDialog name="RAW_NAME/日本" onOpenChange={close} onConfirm={confirm} />,
          errors
        )
      );
      expect(screen.getByRole("alertdialog")).toHaveTextContent(
        t("title", { name: "RAW_NAME/日本" })
      );
      expect(screen.getByText(t("history"))).toBeInTheDocument();
      expect(screen.getByRole("button", { name: t("confirm") })).toBeDisabled();
      fireEvent.click(screen.getByRole("checkbox", { name: t("acknowledge") }));
      expect(screen.getByRole("button", { name: t("confirm") })).toBeEnabled();
      view.rerender(
        provider(
          locale,
          <AnonymizeUserDialog name="OTHER_USER" onOpenChange={close} onConfirm={confirm} />,
          errors
        )
      );
      expect(screen.getByRole("button", { name: t("confirm") })).toBeDisabled();
      expect(screen.getByRole("checkbox")).not.toBeChecked();
      fireEvent.click(screen.getByRole("checkbox"));
      fireEvent.click(screen.getByRole("button", { name: t("confirm") }));
      await waitFor(() => expect(confirm).toHaveBeenCalledTimes(1));
      expect(close).not.toHaveBeenCalled();
      expect(screen.getByRole("alertdialog")).toBeInTheDocument();
      expect(errors).not.toHaveBeenCalled();
    }
  );
});
