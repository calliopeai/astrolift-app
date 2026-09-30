"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { useApolloClient } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { useMe } from "@/graphql/user/user.hooks";
import { PLAYGROUND_ENDPOINTS_PAGE } from "@/graphql/playground/playground.queries";
import type { PlaygroundEndpointsPageQuery } from "@/graphql/__generated__/operations";
import {
  deleteSession,
  listSavedSessions,
  loadSession,
  MAX_MESSAGES,
  saveSession,
  toggleStar,
  type SavedMessage,
  type SavedSessionIndexEntry,
} from "./saved-sessions";
import { usePromptRelay } from "./use-prompt-relay";
import type { EndpointOption, PromptError } from "./playground.types";

export type PlaygroundTab = "chat" | "batch";
type Session = {
  scope: string;
  id: string;
  title: string;
  messages: SavedMessage[];
  activeSavedId: string | null;
};
const newSession = (scope: string): Session => ({
  scope,
  id: crypto.randomUUID(),
  title: "",
  messages: [],
  activeSavedId: null,
});

/** Actual Apollo prompts and bounded browser-local observations; no generated replies. */
export function usePlayground() {
  const client = useApolloClient();
  const requestedSession = useSearchParams().get("session");
  const t = useTranslations("playground");
  const copy = useRef(t);
  useLayoutEffect(() => {
    copy.current = t;
  }, [t]);
  const { org, loading: orgLoading, error: orgError } = useActiveOrg();
  const { user, loading: userLoading, error: userError } = useMe();
  const scope = org?.id && user?.id ? `${org.id}:${user.id}` : "";
  const identityLoading = !!orgLoading || !!userLoading;
  const identityUnavailable = !identityLoading && (!scope || !!orgError || !!userError);
  const [session, setSession] = useState<Session>(() => newSession(""));
  const visible =
    session.scope === scope
      ? session
      : { ...session, title: "", messages: [], activeSavedId: null };
  const [selected, setSelected] = useState({ scope: "", id: "", name: "" });
  const model = selected.scope === scope ? selected.id : "";
  const modelName = selected.scope === scope ? selected.name : "";
  const relay = usePromptRelay(scope, user?.id ?? "", model);
  const [tab, setTab] = useState<PlaygroundTab>("chat");
  const [prompt, setPrompt] = useState("");
  const [error, setError] = useState<PromptError | null>(null);
  const [saved, setSaved] = useState<{ scope: string; rows: SavedSessionIndexEntry[] }>({
    scope: "",
    rows: [],
  });
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [retry, setRetry] = useState(0);
  const catalogKey = `${scope}:${search}:${page}:${retry}`;
  const [catalog, setCatalog] = useState<{
    key: string;
    rows: EndpointOption[];
    total: number;
    error: boolean;
    loading: boolean;
  }>({ key: "", rows: [], total: 0, error: false, loading: true });
  const identityKey = `${scope}:${model}`;
  const [epoch, setEpoch] = useState({ key: identityKey, version: 0 });
  if (epoch.key !== identityKey) setEpoch({ key: identityKey, version: epoch.version + 1 });
  const identity = useRef({ scope, version: 0, model, mounted: true });
  useLayoutEffect(() => {
    identity.current = { ...identity.current, scope, model, version: epoch.version };
  }, [scope, model, epoch.version]);
  useEffect(() => {
    identity.current.mounted = true;
    return () => {
      identity.current.mounted = false;
      identity.current.version += 1;
    };
  }, []);
  useEffect(() => {
    setSession(newSession(scope));
    setPrompt("");
    setError(null);
    setTab("chat");
    setSearch("");
    setPage(1);
    if (!scope) {
      setSaved({ scope, rows: [] });
      return;
    }
    try {
      setSaved({ scope, rows: listSavedSessions(scope) });
      if (requestedSession) {
        const restored = loadSession(scope, requestedSession);
        if (restored) {
          setSelected({ scope, id: restored.model, name: restored.modelName });
          setSession({
            scope,
            id: restored.id,
            title: restored.title,
            messages: restored.messages,
            activeSavedId: restored.id,
          });
        } else toast.error(copy.current("loadFailed"));
      }
    } catch {
      setSaved({ scope, rows: [] });
      toast.error(copy.current("loadFailed"));
    }
  }, [scope, requestedSession]);
  useEffect(() => {
    if (!scope) return;
    let cancelled = false;
    const timer = setTimeout(() => {
      void client
        .query<PlaygroundEndpointsPageQuery>({
          query: PLAYGROUND_ENDPOINTS_PAGE,
          variables: {
            search: search.trim() || null,
            page,
            pageSize: 10,
          },
          fetchPolicy: "no-cache",
          context: { queryDeduplication: false },
        })
        .then((result) => {
          if (cancelled) return;
          const data = result.data?.astroliftModelEndpointsPage;
          if (!data) throw new Error("No endpoint page");
          setCatalog({
            key: catalogKey,
            rows: data.items,
            total: data.totalCount ?? 0,
            error: false,
            loading: false,
          });
        })
        .catch(() => {
          if (!cancelled)
            setCatalog({ key: catalogKey, rows: [], total: 0, error: true, loading: false });
        });
    }, 200);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [client, scope, catalogKey, search, page]);
  const currentCatalog =
    catalog.key === catalogKey ? catalog : { rows: [], total: 0, error: false, loading: !!scope };
  const refresh = () => setSaved({ scope, rows: listSavedSessions(scope) });
  const onNew = () => {
    setSession(newSession(scope));
    setPrompt("");
    setError(null);
    setTab("chat");
  };
  const sending = useRef(false);
  const onSend = async () => {
    if (
      identity.current.scope !== scope ||
      identity.current.model !== model ||
      identity.current.version !== epoch.version ||
      sending.current ||
      !scope ||
      !model ||
      !relay.canSend ||
      relay.loading ||
      visible.messages.length > MAX_MESSAGES - 2
    )
      return;
    const text = prompt.trim();
    if (!text || text.length > relay.maxPromptChars) {
      setError("invalid");
      return;
    }
    sending.current = true;
    const version = identity.current.version;
    setError(null);
    setPrompt("");
    setSession((s) => ({ ...s, messages: [...s.messages, { role: "user", content: text }] }));
    const result = await relay.invoke(text);
    sending.current = false;
    if (!identity.current.mounted || identity.current.version !== version) return;
    if (!result.ok) {
      setError(result.error ?? "failed");
      return;
    }
    setSession((s) => ({
      ...s,
      messages: [
        ...s.messages,
        {
          role: "assistant",
          content: result.reply,
          totalTokens: result.totalTokens,
          latencyMs: result.latencyMs,
        },
      ],
    }));
  };
  const onSave = () => {
    if (!scope || !model || !visible.messages.length || relay.loading) return;
    try {
      saveSession(scope, {
        id: visible.id,
        title:
          visible.title.trim() ||
          visible.messages.find((m) => m.role === "user")?.content.slice(0, 120) ||
          t("title"),
        model,
        modelName: modelName.slice(0, 200),
        messages: visible.messages,
      });
      setSession((s) => ({ ...s, activeSavedId: s.id }));
      refresh();
      toast.success(t("saved"));
    } catch {
      toast.error(t("saveFailed"));
    }
  };
  const onLoad = (id: string) => {
    if (!scope || relay.loading) return;
    try {
      const row = loadSession(scope, id);
      if (!row) throw new Error("Missing session");
      setSelected({ scope, id: row.model, name: row.modelName });
      setSession({
        scope,
        id: row.id,
        title: row.title,
        messages: row.messages,
        activeSavedId: row.id,
      });
      setPrompt("");
      setError(null);
      setTab("chat");
    } catch {
      toast.error(t("loadFailed"));
    }
  };
  const onShare = async () => {
    if (!scope || !visible.messages.length) return;
    try {
      await navigator.clipboard.writeText(
        JSON.stringify(
          { title: visible.title, model, modelName, messages: visible.messages },
          null,
          2
        )
      );
      toast.success(t("copied"));
    } catch {
      toast.error(t("copyFailed"));
    }
  };
  return {
    tab,
    setTab,
    title: visible.title,
    setTitle: (title: string) => setSession((s) => ({ ...s, title })),
    messages: visible.messages,
    prompt: session.scope === scope ? prompt : "",
    setPrompt,
    model,
    modelName,
    setModel: (id: string) => {
      if (relay.loading) return;
      const row = currentCatalog.rows.find((m) => m.id === id);
      if (!row) return;
      setSelected({ scope, id, name: row.name });
      onNew();
    },
    models: currentCatalog.rows,
    search,
    setSearch: (value: string) => {
      setSearch(value.slice(0, 200));
      setPage(1);
    },
    page,
    totalCount: currentCatalog.total,
    setPage,
    catalogLoading: identityLoading || currentCatalog.loading,
    catalogError: identityUnavailable || currentCatalog.error,
    onCatalogRetry: () => setRetry((n) => n + 1),
    readiness: identityUnavailable ? ("refused" as const) : relay.readiness,
    onReadinessRetry: relay.retry,
    maxPromptChars: relay.maxPromptChars,
    maxOutputTokens: relay.maxOutputTokens,
    maxWaitSeconds: relay.maxWaitSeconds,
    canSend: relay.canSend && visible.messages.length <= MAX_MESSAGES - 2,
    loading: relay.loading,
    error: session.scope === scope ? error : null,
    savedLoading: identityLoading || (!!scope && saved.scope !== scope),
    savedSessions: saved.scope === scope ? saved.rows : [],
    activeSavedId: visible.activeSavedId,
    onSend,
    onSave,
    onShare,
    onLoad,
    onNew,
    onDelete: (id: string) => {
      if (!scope) return;
      try {
        deleteSession(scope, id);
        if (visible.activeSavedId === id) onNew();
        refresh();
      } catch {
        toast.error(t("saveFailed"));
      }
    },
    onStar: (id: string) => {
      if (!scope) return;
      try {
        toggleStar(scope, id);
        refresh();
      } catch {
        toast.error(t("saveFailed"));
      }
    },
    invoke: relay.invoke,
    contextKey: relay.contextKey,
  };
}
