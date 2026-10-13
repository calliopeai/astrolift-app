"use client";

import * as React from "react";

import { getClientToken } from "@/lib/auth/token-store";
import { getActiveOrgGuid } from "@/lib/identity/active-org";

export type SupportEvent = {
  id: string;
  action: string;
  author: string | null;
  at: string;
  detail: string | null;
  from: string | null;
  to: string | null;
};

export type SupportTicket = {
  id: string;
  title: string;
  body: string;
  status: string;
  severity: number;
  created_at: string;
  updated_at: string;
  coverage_summary: {
    reference: string | null;
    package: string | null;
    window: string | null;
    continuous_for_severity: boolean;
  } | null;
  events: SupportEvent[];
};

export type SupportDraft = { title: string; body: string; severity: number };

/** A refusal code the screen names: the server's, or UNAVAILABLE for anything else. */
export type SupportErrorCode =
  | "SUPPORT_COVERAGE_REQUIRED"
  | "SUPPORT_REQUEST_INVALID"
  | "PERMISSION_DENIED"
  | "NOT_FOUND"
  | "SUPPORT_UNAVAILABLE";

const KNOWN: SupportErrorCode[] = [
  "SUPPORT_COVERAGE_REQUIRED",
  "SUPPORT_REQUEST_INVALID",
  "PERMISSION_DENIED",
  "NOT_FOUND",
  "SUPPORT_UNAVAILABLE",
];

class SupportError extends Error {
  constructor(
    readonly code: SupportErrorCode,
    readonly status: number
  ) {
    super(code);
  }
}

const BASE = "/api/support/v1/tickets/";

async function call<T>(path: string, init?: { method: "POST"; body: unknown }): Promise<T> {
  const token = await getClientToken();
  const org = getActiveOrgGuid();
  const response = await fetch(path, {
    method: init?.method ?? "GET",
    headers: {
      Accept: "application/json",
      "x-platform": "web",
      ...(init ? { "Content-Type": "application/json" } : {}),
      ...(token ? { Authorization: token } : {}),
      ...(org ? { "X-Astrolift-Organization": org } : {}),
    },
    body: init ? JSON.stringify(init.body) : undefined,
  });
  const payload = (await response.json().catch(() => ({}))) as { code?: string } & T;
  if (!response.ok) {
    const code = KNOWN.find((known) => known === payload.code);
    throw new SupportError(
      code ?? (response.status === 404 ? "NOT_FOUND" : "SUPPORT_UNAVAILABLE"),
      response.status
    );
  }
  return payload;
}

const codeOf = (cause: unknown): SupportErrorCode =>
  cause instanceof SupportError ? cause.code : "SUPPORT_UNAVAILABLE";

export const EMPTY_DRAFT: SupportDraft = { title: "", body: "", severity: 3 };

/** The Support page's state: the case list, one open case, a new case and a reply. */
export function useSupport() {
  const [tickets, setTickets] = React.useState<SupportTicket[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<SupportErrorCode | null>(null);
  const [unavailable, setUnavailable] = React.useState(false);
  const [selected, setSelected] = React.useState<SupportTicket | null>(null);
  const [detailLoading, setDetailLoading] = React.useState(false);
  const [detailError, setDetailError] = React.useState<SupportErrorCode | null>(null);
  const [draft, setDraft] = React.useState<SupportDraft>(EMPTY_DRAFT);
  const [creating, setCreating] = React.useState(false);
  const [createError, setCreateError] = React.useState<SupportErrorCode | null>(null);
  const [createdId, setCreatedId] = React.useState<string | null>(null);
  const [reply, setReply] = React.useState("");
  const [replying, setReplying] = React.useState(false);
  const [replyError, setReplyError] = React.useState<SupportErrorCode | null>(null);

  const load = React.useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const page = await call<{ tickets: SupportTicket[] }>(BASE);
      setTickets(page.tickets ?? []);
    } catch (cause) {
      // The module answers 404 when it is switched off for this install.
      if (cause instanceof SupportError && cause.status === 404) setUnavailable(true);
      else setError(codeOf(cause));
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    void load();
  }, [load]);

  const open = async (id: string | null) => {
    setReply("");
    setReplyError(null);
    setDetailError(null);
    if (id === null) {
      setSelected(null);
      return;
    }
    setSelected(tickets.find((row) => row.id === id) ?? null);
    setDetailLoading(true);
    try {
      setSelected((await call<{ ticket: SupportTicket }>(`${BASE}${id}/`)).ticket);
    } catch (cause) {
      setDetailError(codeOf(cause));
    } finally {
      setDetailLoading(false);
    }
  };

  return {
    tickets,
    loading,
    error,
    unavailable,
    onRetry: (): void => void load(),
    selected,
    detailLoading,
    detailError,
    onOpen: (id: string | null): void => void open(id),
    draft,
    onDraft: (next: SupportDraft) => {
      setDraft(next);
      setCreateError(null);
      setCreatedId(null);
    },
    creating,
    createError,
    createdId,
    onCreate: async () => {
      setCreating(true);
      setCreateError(null);
      try {
        const { ticket } = await call<{ ticket: SupportTicket }>(BASE, {
          method: "POST",
          body: { title: draft.title.trim(), body: draft.body.trim(), severity: draft.severity },
        });
        setTickets((rows) => [ticket, ...rows.filter((row) => row.id !== ticket.id)]);
        setCreatedId(ticket.id);
        setDraft(EMPTY_DRAFT);
      } catch (cause) {
        setCreateError(codeOf(cause));
      } finally {
        setCreating(false);
      }
    },
    reply,
    onReply: (value: string) => {
      setReply(value);
      setReplyError(null);
    },
    replying,
    replyError,
    onSendReply: async () => {
      if (!selected) return;
      setReplying(true);
      setReplyError(null);
      try {
        const { ticket } = await call<{ ticket: SupportTicket }>(`${BASE}${selected.id}/`, {
          method: "POST",
          body: { message: reply.trim() },
        });
        setSelected(ticket);
        setTickets((rows) => rows.map((row) => (row.id === ticket.id ? ticket : row)));
        setReply("");
      } catch (cause) {
        setReplyError(codeOf(cause));
      } finally {
        setReplying(false);
      }
    },
  };
}

export type SupportState = ReturnType<typeof useSupport>;
