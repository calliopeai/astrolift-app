"use client";

/** Browser-local, bounded observed prompt records, isolated by actual org/user. */
import { buildCsv } from "@/components/list/exportCsv";

const MAX_SESSIONS = 30;
export const MAX_MESSAGES = 40;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export type SavedMessage = {
  role: "user" | "assistant";
  content: string;
  latencyMs?: number | null;
  totalTokens?: number | null;
};
export type SavedSession = {
  schema: 2;
  id: string;
  title: string;
  model: string;
  modelName: string;
  messages: SavedMessage[];
  createdAt: string;
  updatedAt: string;
  starred: boolean;
};
export type SavedSessionIndexEntry = Pick<
  SavedSession,
  "id" | "title" | "model" | "modelName" | "updatedAt" | "starred"
> & { messageCount: number };
const key = (scope: string) => {
  if (!scope || scope.length > 160 || !/^[a-zA-Z0-9:_-]+$/.test(scope))
    throw new Error("Invalid local scope");
  return `playground:v2:${scope}`;
};
function observed(value: unknown): value is number | null | undefined {
  return (
    value == null ||
    (typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 1e9)
  );
}
function valid(value: unknown): value is SavedSession {
  if (!value || typeof value !== "object") return false;
  const s = value as Record<string, unknown>;
  return (
    s.schema === 2 &&
    typeof s.id === "string" &&
    UUID.test(s.id) &&
    typeof s.model === "string" &&
    UUID.test(s.model) &&
    typeof s.title === "string" &&
    s.title.length <= 120 &&
    typeof s.modelName === "string" &&
    s.modelName.length <= 200 &&
    typeof s.starred === "boolean" &&
    typeof s.createdAt === "string" &&
    Number.isFinite(Date.parse(s.createdAt)) &&
    typeof s.updatedAt === "string" &&
    Number.isFinite(Date.parse(s.updatedAt)) &&
    Array.isArray(s.messages) &&
    s.messages.length <= MAX_MESSAGES &&
    s.messages.every((m: unknown) => {
      if (!m || typeof m !== "object") return false;
      const row = m as Record<string, unknown>;
      return (
        (row.role === "user" || row.role === "assistant") &&
        typeof row.content === "string" &&
        row.content.length <= (row.role === "user" ? 4000 : 8000) &&
        observed(row.latencyMs) &&
        observed(row.totalTokens)
      );
    })
  );
}
function rows(scope: string): SavedSession[] {
  if (typeof window === "undefined") return [];
  const raw = localStorage.getItem(key(scope));
  if (!raw || raw.length > 12000000) return [];
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return [];
  }
  return Array.isArray(parsed) && parsed.length <= MAX_SESSIONS ? parsed.filter(valid) : [];
}
function write(scope: string, records: SavedSession[]) {
  localStorage.setItem(key(scope), JSON.stringify(records));
}
export function listSavedSessions(scope: string): SavedSessionIndexEntry[] {
  return rows(scope)
    .sort((a, b) => Number(b.starred) - Number(a.starred) || b.updatedAt.localeCompare(a.updatedAt))
    .map((s) => ({
      id: s.id,
      title: s.title,
      model: s.model,
      modelName: s.modelName,
      updatedAt: s.updatedAt,
      starred: s.starred,
      messageCount: s.messages.length,
    }));
}
export function loadSession(scope: string, id: string): SavedSession | null {
  return UUID.test(id) ? (rows(scope).find((s) => s.id === id) ?? null) : null;
}
export function saveSession(
  scope: string,
  session: Omit<SavedSession, "schema" | "createdAt" | "updatedAt" | "starred">
): SavedSession {
  const previous = rows(scope);
  const old = previous.find((s) => s.id === session.id);
  const now = new Date().toISOString();
  const full: SavedSession = {
    ...session,
    schema: 2,
    createdAt: old?.createdAt ?? now,
    updatedAt: now,
    starred: old?.starred ?? false,
  };
  if (!valid(full)) throw new Error("Invalid local session");
  const kept = previous
    .filter((s) => s.id !== full.id)
    .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt))
    .slice(0, MAX_SESSIONS - 1);
  write(scope, [full, ...kept]);
  return full;
}
export function deleteSession(scope: string, id: string): void {
  write(
    scope,
    rows(scope).filter((s) => s.id !== id)
  );
}
export function toggleStar(scope: string, id: string): boolean {
  const records = rows(scope);
  const saved = records.find((s) => s.id === id);
  if (!saved) return false;
  saved.starred = !saved.starred;
  write(scope, records);
  return saved.starred;
}

// ─── Batch export ─────────────────────────────────────────────────────

export type BatchResult = {
  input: string;
  output: string;
  ok: boolean;
  error?: import("./playground.types").PromptError;
  latencyMs?: number | null;
  totalTokens?: number | null;
};

export function batchToCsv(results: BatchResult[]): string {
  return buildCsv(results, [
    { header: "input", value: (r) => r.input },
    { header: "output", value: (r) => r.output },
    { header: "ok", value: (r) => r.ok },
    { header: "error", value: (r) => r.error },
    { header: "latency_ms", value: (r) => r.latencyMs },
    { header: "total_tokens", value: (r) => r.totalTokens },
  ]);
}

/** Match reported metrics to their actual preceding prompt, excluding later failed attempts. */
export function latestObservedPrompt(messages: SavedMessage[]) {
  const index = messages.findLastIndex((m) => m.role === "assistant");
  const reply = index >= 0 ? messages[index] : undefined;
  const prompt = (index >= 0 ? messages.slice(0, index) : messages).findLast(
    (m) => m.role === "user"
  );
  return { prompt, reply };
}

export function batchToJsonl(results: BatchResult[]): string {
  return results.map((r) => JSON.stringify(r)).join("\n");
}

export function downloadBlob(content: string, filename: string, mime: string): void {
  if (typeof window === "undefined") return;
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
