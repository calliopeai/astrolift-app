import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SupportClient } from "@/app/(app)/support/support-client";
import en from "@/messages/en.json";

import { TICKET } from "./support.fixtures";

vi.mock("@/lib/auth/token-store", () => ({ getClientToken: async () => null }));
vi.mock("@/lib/identity/active-org", () => ({
  getActiveOrgGuid: () => "a0000000-0000-4000-8000-000000000002",
}));

const c = en.support;
type Call = { url: string; method: string; headers: Record<string, string>; body: unknown };
let calls: Call[];
let replies: { status: number; body: unknown }[];

beforeEach(() => {
  calls = [];
  replies = [];
  vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
    calls.push({
      url,
      method: init.method ?? "GET",
      headers: init.headers as Record<string, string>,
      body: init.body ? JSON.parse(init.body as string) : null,
    });
    const reply = replies.shift() ?? { status: 200, body: { tickets: [] } };
    return new Response(JSON.stringify(reply.body), { status: reply.status });
  });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function mount() {
  return render(
    <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
      <SupportClient />
    </NextIntlClientProvider>
  );
}

describe("Support page over its REST surface (#1809)", () => {
  it("lists the organization's cases with the UI header and active org", async () => {
    replies.push({ status: 200, body: { tickets: [TICKET] } });
    mount();
    await waitFor(() => expect(screen.getByText(TICKET.title)).toBeInTheDocument());
    expect(calls[0]).toMatchObject({ url: "/api/support/v1/tickets/", method: "GET" });
    expect(calls[0].headers["x-platform"]).toBe("web");
    expect(calls[0].headers["X-Astrolift-Organization"]).toBe(
      "a0000000-0000-4000-8000-000000000002"
    );
  });

  it("shows one unavailable state when the module is off", async () => {
    replies.push({ status: 404, body: { error: "support unavailable" } });
    mount();
    await waitFor(() => expect(screen.getByText(c.unavailable)).toBeInTheDocument());
    expect(screen.queryByLabelText(c.caseTitle)).not.toBeInTheDocument();
  });

  it("opens a case with only summary, description and severity, and names a coverage refusal", async () => {
    mount();
    await waitFor(() => expect(screen.getByText(c.empty)).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText(c.caseTitle), { target: { value: " Deploys stuck " } });
    fireEvent.change(screen.getByLabelText(c.caseBody), { target: { value: "Since 09:00" } });
    fireEvent.change(screen.getByLabelText(c.severity), { target: { value: "1" } });
    replies.push({ status: 402, body: { code: "SUPPORT_COVERAGE_REQUIRED", error: "x" } });
    fireEvent.click(screen.getByRole("button", { name: c.submit }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(c.errors.coverage));
    expect(calls[1]).toMatchObject({
      url: "/api/support/v1/tickets/",
      method: "POST",
      body: { title: "Deploys stuck", body: "Since 09:00", severity: 1 },
    });

    replies.push({ status: 201, body: { ticket: TICKET } });
    fireEvent.click(screen.getByRole("button", { name: c.submit }));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent(TICKET.id));
    expect(screen.getByText(TICKET.title)).toBeInTheDocument();
  });

  it("reads a case by its id and posts a reply to it", async () => {
    replies.push({ status: 200, body: { tickets: [TICKET] } });
    mount();
    await waitFor(() => expect(screen.getByText(TICKET.title)).toBeInTheDocument());
    replies.push({ status: 200, body: { ticket: TICKET } });
    fireEvent.click(screen.getByText(TICKET.title));
    await waitFor(() => expect(screen.getByLabelText(c.reply)).toBeInTheDocument());
    expect(calls[1]).toMatchObject({ url: `/api/support/v1/tickets/${TICKET.id}/`, method: "GET" });
    fireEvent.change(screen.getByLabelText(c.reply), { target: { value: " Output attached " } });
    replies.push({ status: 200, body: { ticket: TICKET } });
    fireEvent.click(screen.getByRole("button", { name: c.sendReply }));
    await waitFor(() => expect(calls).toHaveLength(3));
    expect(calls[2]).toMatchObject({
      url: `/api/support/v1/tickets/${TICKET.id}/`,
      method: "POST",
      body: { message: "Output attached" },
    });
  });
});
