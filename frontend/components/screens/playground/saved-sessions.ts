"use client";

/**
 * Local-storage layer for playground saved sessions (#437 scope C).
 *
 * Sessions are stored under one key per session
 * (``playground:saved:<uuid>``) plus an index key
 * (``playground:saved:index``) so the sidebar can list them without
 * iterating storage on every render. The schema is intentionally flat
 * — no nested messages array on the index — to keep the index payload
 * small and let the list render with a cached read.
 *
 * Versioning: a top-level ``schema`` field starts at 1 so future
 * shape changes can migrate forward without losing user data.
 */

const INDEX_KEY = "playground:saved:index";
const SESSION_PREFIX = "playground:saved:";

export type SavedMessage = {
  role: "user" | "assistant";
  content: string;
};

export type SavedSession = {
  schema: 1;
  id: string;
  title: string;
  model: string;
  messages: SavedMessage[];
  createdAt: string;
  updatedAt: string;
  starred: boolean;
};

export type SavedSessionIndexEntry = {
  id: string;
  title: string;
  model: string;
  updatedAt: string;
  starred: boolean;
  messageCount: number;
};

const isClient = (): boolean => typeof window !== "undefined";

function safeParse<T>(raw: string | null): T | null {
  if (!raw) return null;
  try {
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}

function readIndex(): SavedSessionIndexEntry[] {
  if (!isClient()) return [];
  const list = safeParse<SavedSessionIndexEntry[]>(localStorage.getItem(INDEX_KEY));
  return Array.isArray(list) ? list : [];
}

function writeIndex(entries: SavedSessionIndexEntry[]): void {
  if (!isClient()) return;
  localStorage.setItem(INDEX_KEY, JSON.stringify(entries));
}

export function listSavedSessions(): SavedSessionIndexEntry[] {
  // Sort starred first, then by most recently updated. Stable
  // ordering means the sidebar doesn't reflow on every render.
  const entries = readIndex();
  return [...entries].sort((a, b) => {
    if (a.starred !== b.starred) return a.starred ? -1 : 1;
    return b.updatedAt.localeCompare(a.updatedAt);
  });
}

export function loadSession(id: string): SavedSession | null {
  if (!isClient()) return null;
  return safeParse<SavedSession>(localStorage.getItem(SESSION_PREFIX + id));
}

export function saveSession(
  session: Omit<SavedSession, "schema" | "createdAt" | "updatedAt" | "starred"> & {
    starred?: boolean;
    createdAt?: string;
  }
): SavedSession {
  if (!isClient()) {
    throw new Error("saveSession requires a browser environment");
  }
  const now = new Date().toISOString();
  const full: SavedSession = {
    schema: 1,
    id: session.id,
    title: session.title || untitledFromMessages(session.messages),
    model: session.model,
    messages: session.messages,
    createdAt: session.createdAt ?? now,
    updatedAt: now,
    starred: session.starred ?? false,
  };
  localStorage.setItem(SESSION_PREFIX + full.id, JSON.stringify(full));

  const index = readIndex().filter((e) => e.id !== full.id);
  index.push({
    id: full.id,
    title: full.title,
    model: full.model,
    updatedAt: full.updatedAt,
    starred: full.starred,
    messageCount: full.messages.length,
  });
  writeIndex(index);
  return full;
}

export function deleteSession(id: string): void {
  if (!isClient()) return;
  localStorage.removeItem(SESSION_PREFIX + id);
  writeIndex(readIndex().filter((e) => e.id !== id));
}

export function toggleStar(id: string): boolean {
  if (!isClient()) return false;
  const session = loadSession(id);
  if (!session) return false;
  session.starred = !session.starred;
  session.updatedAt = new Date().toISOString();
  localStorage.setItem(SESSION_PREFIX + id, JSON.stringify(session));
  const index = readIndex().map((e) =>
    e.id === id ? { ...e, starred: session.starred, updatedAt: session.updatedAt } : e
  );
  writeIndex(index);
  return session.starred;
}

function untitledFromMessages(messages: SavedMessage[]): string {
  const firstUser = messages.find((m) => m.role === "user");
  if (firstUser) {
    return firstUser.content.length > 60 ? `${firstUser.content.slice(0, 57)}…` : firstUser.content;
  }
  return "Untitled session";
}

// ─── Shareable links ───────────────────────────────────────────────────
//
// Shared sessions ride a URL hash so we never put conversation
// content into the server's request logs. The hash is base64-URL of
// the JSON payload — small (~few KB) and survives copy-paste.

const SHARE_HASH_PREFIX = "share=";

export function encodeShareHash(session: SavedSession): string {
  const trimmed = {
    schema: session.schema,
    title: session.title,
    model: session.model,
    messages: session.messages,
  };
  const json = JSON.stringify(trimmed);
  // base64-URL: replace + with -, / with _, strip trailing =
  const b64 = btoa(unescape(encodeURIComponent(json)));
  const urlSafe = b64.replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  return `#${SHARE_HASH_PREFIX}${urlSafe}`;
}

export function decodeShareHash(
  hash: string
): Pick<SavedSession, "title" | "model" | "messages"> | null {
  if (!hash) return null;
  const trimmed = hash.startsWith("#") ? hash.slice(1) : hash;
  if (!trimmed.startsWith(SHARE_HASH_PREFIX)) return null;
  const payload = trimmed.slice(SHARE_HASH_PREFIX.length);
  if (!payload) return null;
  try {
    const padded = payload.replace(/-/g, "+").replace(/_/g, "/");
    const pad = padded.length % 4 === 0 ? "" : "=".repeat(4 - (padded.length % 4));
    const json = decodeURIComponent(escape(atob(padded + pad)));
    const parsed = JSON.parse(json) as {
      schema?: number;
      title?: string;
      model?: string;
      messages?: SavedMessage[];
    };
    if (!parsed.messages || !Array.isArray(parsed.messages)) return null;
    return {
      title: parsed.title || "Shared session",
      model: parsed.model || "Genesis",
      messages: parsed.messages,
    };
  } catch {
    return null;
  }
}

// ─── Batch export ─────────────────────────────────────────────────────

export type BatchResult = {
  input: string;
  output: string;
  ok: boolean;
  error?: string;
};

export function batchToCsv(results: BatchResult[]): string {
  const header = "input,output,ok,error";
  const rows = results.map((r) =>
    [r.input, r.output, r.ok ? "true" : "false", r.error ?? ""]
      .map((cell) => `"${cell.replace(/"/g, '""')}"`)
      .join(",")
  );
  return [header, ...rows].join("\n");
}

export function batchToJsonl(results: BatchResult[]): string {
  return results.map((r) => JSON.stringify(r)).join("\n");
}

export function downloadBlob(content: string, filename: string, mime: string): void {
  if (!isClient()) return;
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
