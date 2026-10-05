import { ApolloClient, HttpLink, InMemoryCache } from "@apollo/client";
import { ApolloProvider } from "@apollo/client/react";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { buildSchema, graphql } from "graphql";
import { NextIntlClientProvider } from "next-intl";
import { readFileSync } from "node:fs";
import { createServer, type Server } from "node:http";
import type { AddressInfo } from "node:net";
import { beforeEach, afterEach, it, expect, vi } from "vitest";
import en from "@/messages/en.json";
import { InstallAlertMailClient } from "./InstallAlertMailClient";
import { ALERT_PANEL, ALERT_TEST } from "./InstallAlertMailPanel.stories";
const identity = vi.hoisted(() => ({
  actor: "a0000000-0000-4000-8000-000000000001",
  org: "a0000000-0000-4000-8000-000000000002",
}));
vi.mock("@/graphql/user/user.hooks", () => ({
  useMe: () => ({ user: { id: identity.actor }, loading: false, error: null }),
}));
vi.mock("@/graphql/identity/identity.hooks", () => ({
  useActiveOrg: () => ({ org: { id: identity.org }, loading: false, error: null }),
}));
const schema = buildSchema(readFileSync(`${process.cwd()}/schema.graphql`, "utf8")),
  c = en.installAlertMail;
let server: Server,
  client: ApolloClient,
  view: ReturnType<typeof render>,
  support: typeof ALERT_PANEL.support,
  rows: (typeof ALERT_TEST)[],
  calls: { name: string; v: Record<string, unknown> }[],
  errors: string[];
let lose: boolean,
  refuseAfterSend: boolean,
  historyDenied: boolean,
  permissionDenied: boolean,
  forged: boolean,
  noHistory: boolean,
  hold: boolean,
  release: (() => void) | null;
beforeEach(async () => {
  sessionStorage.clear();
  identity.actor = "a0000000-0000-4000-8000-000000000001";
  identity.org = "a0000000-0000-4000-8000-000000000002";
  support = structuredClone(ALERT_PANEL.support);
  rows = [];
  calls = [];
  errors = [];
  lose = refuseAfterSend = historyDenied = permissionDenied = forged = noHistory = hold = false;
  release = null;
  server = createServer(async (req, res) => {
    let body = "";
    for await (const b of req) body += b;
    const input = JSON.parse(body);
    calls.push({ name: input.operationName, v: input.variables });
    const result = await graphql({
      schema,
      source: input.query,
      variableValues: input.variables,
      rootValue: {
        installAlertMailSupport: () => {
          if (permissionDenied) throw new Error("Current operator required");
          return support;
        },
        installAlertMailTestsPage: ({
          eventKind,
          after,
        }: {
          eventKind: string;
          after: string | null;
        }) => {
          if (historyDenied || permissionDenied) throw new Error("Current own history denied");
          return {
            items: noHistory
              ? []
              : after
                ? rows.slice(1)
                : rows.filter((r) => r.eventKind === eventKind),
            totalCount: rows.length,
            nextCursor: rows.length > 1 && !after ? "opaque-own-cursor" : null,
          };
        },
        sendInstallAlertMailTest: async ({
          input,
        }: {
          input: { requestId: string; eventKind: string; expectedSourceFingerprint: string };
        }) => {
          const row = { ...ALERT_TEST, requestId: input.requestId, eventKind: input.eventKind };
          rows.push(row);
          if (hold)
            await new Promise<void>((r) => {
              release = r;
            });
          if (refuseAfterSend)
            return {
              ok: false,
              errors: [{ code: "PRECONDITION", message: "ALERT_MAIL_SOURCE_CHANGED" }],
              data: null,
            };
          return {
            ok: true,
            errors: [],
            data: forged ? { ...row, requestId: "b0000000-0000-4000-8000-000000000001" } : row,
          };
        },
      },
    });
    if (result.errors) errors.push(...result.errors.map((e) => e.message));
    if (lose && input.operationName === "SendInstallAlertMailTest") {
      res.destroy();
      return;
    }
    res.setHeader("content-type", "application/json");
    res.end(JSON.stringify(result));
  });
  await new Promise<void>((r) => server.listen(0, "127.0.0.1", r));
  client = new ApolloClient({
    cache: new InMemoryCache(),
    link: new HttpLink({
      uri: `http://127.0.0.1:${(server.address() as AddressInfo).port}`,
      fetch,
    }),
    queryDeduplication: false,
  });
  view = render(wrap());
});
function wrap() {
  return (
    <ApolloProvider client={client}>
      <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
        <InstallAlertMailClient />
      </NextIntlClientProvider>
    </ApolloProvider>
  );
}
afterEach(async () => {
  release?.();
  view.unmount();
  client.stop();
  await new Promise<void>((r) => server.close(() => r()));
});
async function loaded() {
  await waitFor(() => expect(screen.getByRole("button", { name: c.review })).toBeEnabled());
}
async function reviewed() {
  await loaded();
  fireEvent.click(screen.getByRole("button", { name: c.review }));
  await waitFor(() => expect(screen.getByRole("button", { name: c.send })).toBeEnabled());
}
const sends = () => calls.filter((x) => x.name === "SendInstallAlertMailTest");
const intent = () =>
  JSON.parse(
    sessionStorage.getItem(
      Object.keys(sessionStorage).find((k) => k.startsWith("astrolift.install-mail"))!
    )!
  );
it("current review sends exact fixed own-mailbox intent once and acceptance is never delivery", async () => {
  await loaded();
  expect(screen.getByRole("button", { name: c.send })).toBeDisabled();
  expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  await reviewed();
  fireEvent.click(screen.getByRole("button", { name: c.send }));
  await screen.findByText(c.acceptance);
  await waitFor(() => expect(sends()).toHaveLength(1));
  expect(sends()[0].v.input).toEqual({
    eventKind: "deploy.failed",
    expectedSourceFingerprint: "a".repeat(64),
    requestId: intent().requestId,
  });
  expect(Object.keys(intent()).sort()).toEqual([
    "event",
    "format",
    "requestId",
    "sourceFingerprint",
  ]);
  expect(JSON.stringify(intent())).not.toContain(ALERT_TEST.recipient);
  expect(screen.getByRole("button", { name: c.send })).toBeDisabled();
  expect(errors).toEqual([]);
});
it.each([
  "ALERT_MAIL_TRANSPORT_UNSUPPORTED",
  "ALERT_MAIL_PREFERENCE_DISABLED",
  "ALERT_MAIL_VERIFIED_TLS_REQUIRED",
])("authoritative %s never enables review/send", async (reason) => {
  support = { ...support!, allowed: false, reason };
  fireEvent.click(screen.getByRole("button", { name: c.refresh }));
  await screen.findByText(reason);
  expect(screen.getByRole("button", { name: c.review })).toBeDisabled();
  expect(sends()).toHaveLength(0);
});
it("current operator refusal withholds SMTP source/history and effects", async () => {
  permissionDenied = true;
  fireEvent.click(screen.getByRole("button", { name: c.refresh }));
  await screen.findByText(c.unavailable);
  expect(screen.getByRole("button", { name: c.send })).toBeDisabled();
  expect(sends()).toHaveLength(0);
});
it("source rotation after review refuses before nonce storage or send", async () => {
  await reviewed();
  support = { ...support!, sourceFingerprint: "b".repeat(64) };
  fireEvent.click(screen.getByRole("button", { name: c.send }));
  await screen.findByText(c.sourceChanged);
  expect(sends()).toHaveLength(0);
  expect(sessionStorage.length).toBe(0);
  expect(screen.getByRole("button", { name: c.send })).toBeDisabled();
});
it("post-acceptance source refusal recovers private accepted intent through history without resend", async () => {
  await reviewed();
  refuseAfterSend = true;
  fireEvent.click(screen.getByRole("button", { name: c.send }));
  await screen.findByText(c.acceptance);
  expect(sends()).toHaveLength(1);
  expect(intent().requestId).toBe(rows[0].requestId);
  expect(screen.getByRole("button", { name: c.send })).toBeDisabled();
});
it("lost reply remains locked with same UUID and no resend until current history confirms terminal", async () => {
  await reviewed();
  lose = true;
  noHistory = true;
  fireEvent.click(screen.getByRole("button", { name: c.send }));
  await screen.findByText(c.uncertain);
  const saved = intent();
  fireEvent.click(screen.getByRole("button", { name: c.refresh }));
  await waitFor(() => expect(screen.getByRole("button", { name: c.send })).toBeDisabled());
  expect(screen.queryByRole("button", { name: c.another })).not.toBeInTheDocument();
  expect(sends()).toHaveLength(1);
  expect(intent()).toEqual(saved);
  noHistory = false;
  fireEvent.click(screen.getByRole("button", { name: c.refresh }));
  await screen.findByText(c.acceptance);
  expect(sends()).toHaveLength(1);
  await waitFor(() => expect(screen.getByRole("button", { name: c.another })).toBeEnabled());
});
it("remounting SENT or UNKNOWN intent reads history but cannot resend or start another", async () => {
  await reviewed();
  lose = true;
  fireEvent.click(screen.getByRole("button", { name: c.send }));
  await screen.findByText(c.acceptance);
  rows[0] = { ...rows[0], status: "sent", acceptedAt: null };
  view.unmount();
  view = render(wrap());
  await screen.findByText(c.acceptance);
  expect(screen.getByRole("button", { name: c.send })).toBeDisabled();
  expect(screen.queryByRole("button", { name: c.another })).not.toBeInTheDocument();
  expect(sends()).toHaveLength(1);
});
it("forged accepted UUID remains accepted-unverified and keeps exact nonce", async () => {
  await reviewed();
  forged = true;
  noHistory = true;
  fireEvent.click(screen.getByRole("button", { name: c.send }));
  await screen.findByText(c.recordUnverified);
  expect(screen.queryByText(c.acceptance)).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: c.send })).toBeDisabled();
  expect(intent().requestId).toBe(rows[0].requestId);
});
it("history withdrawal hides completion and cannot release uncertain intent", async () => {
  await reviewed();
  fireEvent.click(screen.getByRole("button", { name: c.send }));
  await screen.findByText(c.acceptance);
  historyDenied = true;
  fireEvent.click(screen.getByRole("button", { name: c.refresh }));
  await screen.findByText(c.historyUnavailable);
  expect(screen.queryByText(c.acceptance)).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: c.another })).not.toBeInTheDocument();
  expect(sends()).toHaveLength(1);
});
it("actor A-B-A held reply cannot restore prior review or authorize new send", async () => {
  await reviewed();
  hold = true;
  fireEvent.click(screen.getByRole("button", { name: c.send }));
  await waitFor(() => expect(release).not.toBeNull());
  const saved = intent();
  identity.actor = "a0000000-0000-4000-8000-000000000003";
  view.rerender(wrap());
  await loaded();
  expect(screen.queryByText(saved.requestId, { exact: false })).not.toBeInTheDocument();
  identity.actor = "a0000000-0000-4000-8000-000000000001";
  view.rerender(wrap());
  await screen.findByText(c.acceptance);
  await act(async () => {
    release?.();
  });
  expect(screen.getByRole("button", { name: c.send })).toBeDisabled();
  expect(intent()).toEqual(saved);
  expect(sends()).toHaveLength(1);
});
it("event change uses independent fresh support/history with no inherited review", async () => {
  await reviewed();
  fireEvent.change(screen.getByLabelText(c.event), { target: { value: "app.down" } });
  await loaded();
  expect(screen.getByRole("button", { name: c.send })).toBeDisabled();
  expect(
    calls.some((x) => x.name === "InstallAlertMailSupport" && x.v.eventKind === "app.down")
  ).toBe(true);
  expect(sends()).toHaveLength(0);
});
it("unavailable session storage prevents any intentional send", async () => {
  await reviewed();
  const stub = vi.spyOn(Object.getPrototypeOf(sessionStorage), "setItem").mockImplementation(() => {
    throw new Error("Unavailable");
  });
  fireEvent.click(screen.getByRole("button", { name: c.send }));
  await screen.findByText(c.storageUnavailable);
  expect(sends()).toHaveLength(0);
  stub.mockRestore();
});

it("a history row from another event cannot release a prior recorded intent", async () => {
  await reviewed();
  fireEvent.click(screen.getByRole("button", { name: c.send }));
  await screen.findByText(c.acceptance);
  rows[0] = { ...rows[0], eventKind: "app.down" };
  // The server page is event-bound; a missing row cannot convert an unknown intent to success.
  noHistory = true;
  view.unmount();
  view = render(wrap());
  await screen.findByText(c.uncertain);
  expect(screen.queryByRole("button", { name: c.another })).not.toBeInTheDocument();
  expect(sends()).toHaveLength(1);
});
it("another intentional test requires a new review and distinct UUID", async () => {
  await reviewed();
  fireEvent.click(screen.getByRole("button", { name: c.send }));
  await screen.findByText(c.acceptance);
  const original = intent().requestId;
  fireEvent.click(screen.getByRole("button", { name: c.another }));
  await loaded();
  expect(screen.getByRole("button", { name: c.send })).toBeDisabled();
  expect(sends()).toHaveLength(1);
  await reviewed();
  fireEvent.click(screen.getByRole("button", { name: c.send }));
  await waitFor(() => expect(sends()).toHaveLength(2));
  expect(intent().requestId).not.toBe(original);
});
