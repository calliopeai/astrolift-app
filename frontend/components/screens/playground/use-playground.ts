"use client";

import { useEffect, useState } from "react";

import { useBrowserReady } from "@/hooks/use-browser-ready";
import { toast } from "sonner";

import {
  type SavedMessage,
  type SavedSessionIndexEntry,
  decodeShareHash,
  deleteSession,
  encodeShareHash,
  listSavedSessions,
  loadSession,
  saveSession,
  toggleStar,
} from "./saved-sessions";

const MODELS = ["Genesis", "Explorer", "Quantum"];

export type PlaygroundTab = "chat" | "batch";

const initialMessages: SavedMessage[] = [
  { role: "user", content: "Explain the concept of zero-shot prompting." },
  {
    role: "assistant",
    content:
      "Zero-shot prompting means asking a model to perform a task without giving it any examples. The model relies solely on its pre-trained knowledge to generate a response. This is useful when you need quick answers and don't have labeled examples ready.",
  },
];

function newId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

/** Everything the playground screen does outside its markup: session storage, share links, toasts. */
export function usePlayground() {
  const [tab, setTab] = useState<PlaygroundTab>("chat");
  const [sessionId, setSessionId] = useState<string>(() => newId());
  const [title, setTitle] = useState<string>("");
  const [messages, setMessages] = useState<SavedMessage[]>(initialMessages);
  const [prompt, setPrompt] = useState("");
  const [model, setModel] = useState("Genesis");
  const [loading, setLoading] = useState(false);
  const [savedSessions, setSavedSessions] = useState<SavedSessionIndexEntry[]>([]);
  const [activeSavedId, setActiveSavedId] = useState<string | null>(null);

  // Refresh sidebar when persisted state changes.
  const refreshSidebar = () => setSavedSessions(listSavedSessions());

  const browserReady = useBrowserReady();
  const [initialized, setInitialized] = useState(false);
  if (browserReady && !initialized) {
    setInitialized(true);
    setSavedSessions(listSavedSessions());
    const shared = decodeShareHash(window.location.hash);
    if (shared) {
      setMessages(shared.messages);
      setModel(shared.model);
      setTitle(shared.title);
    }
  }
  useEffect(() => {
    if (!browserReady) return;
    const shared = decodeShareHash(window.location.hash);
    if (shared) {
      toast.info(`Loaded shared session: ${shared.title}`);
      window.history.replaceState(null, "", window.location.pathname + window.location.search);
    }
  }, [browserReady]);

  const handleSend = () => {
    if (!prompt.trim()) return;
    const userMsg: SavedMessage = { role: "user", content: prompt };
    setMessages((prev) => [...prev, userMsg]);
    setPrompt("");
    setLoading(true);
    window.setTimeout(() => {
      const assistantMsg: SavedMessage = {
        role: "assistant",
        content: `[${model}] This is a simulated response to: "${userMsg.content}"`,
      };
      setMessages((prev) => [...prev, assistantMsg]);
      setLoading(false);
    }, 600);
  };

  const handleSave = () => {
    if (messages.length === 0) {
      toast.error("Nothing to save — start a conversation first.");
      return;
    }
    const id = activeSavedId ?? sessionId;
    saveSession({
      id,
      title,
      model,
      messages,
    });
    setActiveSavedId(id);
    refreshSidebar();
    toast.success("Session saved");
  };

  const handleShare = async () => {
    if (messages.length === 0) {
      toast.error("Nothing to share — start a conversation first.");
      return;
    }
    const hash = encodeShareHash({
      schema: 1,
      id: activeSavedId ?? sessionId,
      title: title || "Playground session",
      model,
      messages,
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      starred: false,
    });
    const url = `${window.location.origin}${window.location.pathname}${hash}`;
    try {
      await navigator.clipboard.writeText(url);
      toast.success("Share link copied to clipboard");
    } catch {
      toast.error("Could not copy to clipboard");
    }
  };

  const handleLoad = (id: string) => {
    const loaded = loadSession(id);
    if (!loaded) {
      toast.error("Could not load session");
      return;
    }
    setSessionId(loaded.id);
    setActiveSavedId(loaded.id);
    setTitle(loaded.title);
    setModel(loaded.model);
    setMessages(loaded.messages);
    setTab("chat");
  };

  const handleNew = () => {
    setSessionId(newId());
    setActiveSavedId(null);
    setTitle("");
    setMessages(initialMessages);
    setTab("chat");
  };

  const handleDelete = (id: string) => {
    deleteSession(id);
    if (activeSavedId === id) {
      handleNew();
    }
    refreshSidebar();
  };

  const handleStar = (id: string) => {
    toggleStar(id);
    refreshSidebar();
  };

  return {
    models: MODELS,
    tab,
    setTab,
    title,
    setTitle,
    messages,
    prompt,
    setPrompt,
    model,
    setModel,
    loading,
    savedSessions,
    activeSavedId,
    onSend: handleSend,
    onSave: handleSave,
    onShare: handleShare,
    onLoad: handleLoad,
    onNew: handleNew,
    onDelete: handleDelete,
    onStar: handleStar,
  };
}
