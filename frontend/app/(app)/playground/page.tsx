"use client";

import { notFound } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import {
  BotIcon,
  CopyIcon,
  DownloadIcon,
  PlayIcon,
  SaveIcon,
  SendIcon,
  Share2Icon,
  StarIcon,
  StarOffIcon,
  TrashIcon,
  UserIcon,
} from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

import {
  type BatchResult,
  type SavedMessage,
  type SavedSessionIndexEntry,
  batchToCsv,
  batchToJsonl,
  decodeShareHash,
  deleteSession,
  downloadBlob,
  encodeShareHash,
  listSavedSessions,
  loadSession,
  saveSession,
  toggleStar,
} from "./saved-sessions";

import { isRouteEnabled } from "@/lib/route-flags";

const MODELS = ["Genesis", "Explorer", "Quantum"];

type Tab = "chat" | "batch";

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

export default function PlaygroundPage() {
  if (!isRouteEnabled("/playground")) notFound();

  const [tab, setTab] = useState<Tab>("chat");
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

  // Initial mount: hydrate sidebar + replay shared session if the URL
  // hash carries one.
  useEffect(() => {
    refreshSidebar();
    if (typeof window === "undefined") return;
    const hash = window.location.hash;
    const shared = decodeShareHash(hash);
    if (shared) {
      setMessages(shared.messages);
      setModel(shared.model);
      setTitle(shared.title);
      toast.info(`Loaded shared session: ${shared.title}`);
      // Clear the hash so a refresh doesn't re-load forever.
      window.history.replaceState(null, "", window.location.pathname);
    }
  }, []);

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

  return (
    <div className="flex flex-1 gap-4 p-6">
      <Sidebar
        sessions={savedSessions}
        activeId={activeSavedId}
        onLoad={handleLoad}
        onDelete={handleDelete}
        onStar={handleStar}
        onNew={handleNew}
      />

      <div className="flex flex-1 flex-col gap-4">
        <header className="flex flex-wrap items-center gap-3">
          <nav className="flex rounded-md border">
            <button
              type="button"
              onClick={() => setTab("chat")}
              className={cn(
                "rounded-l-md px-4 py-2 text-sm",
                tab === "chat" ? "bg-primary text-primary-foreground" : "hover:bg-accent"
              )}
            >
              Chat
            </button>
            <button
              type="button"
              onClick={() => setTab("batch")}
              className={cn(
                "rounded-r-md border-l px-4 py-2 text-sm",
                tab === "batch" ? "bg-primary text-primary-foreground" : "hover:bg-accent"
              )}
            >
              Batch
            </button>
          </nav>

          <div className="flex items-center gap-2">
            <span className="text-muted-foreground text-sm">Model</span>
            <Select value={model} onValueChange={setModel}>
              <SelectTrigger className="w-36">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {MODELS.map((m) => (
                  <SelectItem key={m} value={m}>
                    {m}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <input
            type="text"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Session title"
            className="border-input min-w-0 flex-1 rounded-md border bg-transparent px-3 py-2 text-sm"
          />

          <div className="flex gap-2">
            <Button size="sm" variant="outline" onClick={handleSave}>
              <SaveIcon className="mr-1 size-3" /> Save
            </Button>
            <Button size="sm" variant="outline" onClick={handleShare}>
              <Share2Icon className="mr-1 size-3" /> Share
            </Button>
          </div>
        </header>

        {tab === "chat" ? (
          <ChatPanel
            messages={messages}
            loading={loading}
            prompt={prompt}
            onPromptChange={setPrompt}
            onSend={handleSend}
          />
        ) : (
          <BatchPanel model={model} />
        )}
      </div>
    </div>
  );
}

function Sidebar({
  sessions,
  activeId,
  onLoad,
  onDelete,
  onStar,
  onNew,
}: {
  sessions: SavedSessionIndexEntry[];
  activeId: string | null;
  onLoad: (id: string) => void;
  onDelete: (id: string) => void;
  onStar: (id: string) => void;
  onNew: () => void;
}) {
  return (
    <aside className="hidden w-64 shrink-0 flex-col gap-2 lg:flex">
      <div className="flex items-center justify-between">
        <h2 className="text-muted-foreground px-1 text-sm font-semibold">Saved sessions</h2>
        <Button size="sm" variant="ghost" onClick={onNew}>
          New
        </Button>
      </div>
      {sessions.length === 0 ? (
        <div className="text-muted-foreground rounded-md border border-dashed p-3 text-center text-xs">
          Nothing saved yet. Save a conversation to keep it here.
        </div>
      ) : (
        <ul className="flex flex-col gap-1">
          {sessions.map((s) => (
            <li key={s.id}>
              <div
                className={cn(
                  "group flex items-center justify-between gap-1 rounded-md px-2 py-1.5",
                  activeId === s.id ? "bg-accent" : "hover:bg-accent/60"
                )}
              >
                <button
                  type="button"
                  onClick={() => onLoad(s.id)}
                  className="min-w-0 flex-1 text-left"
                >
                  <div className="truncate text-sm">{s.title}</div>
                  <div className="text-muted-foreground truncate text-xs">
                    {s.messageCount} msg · {s.model}
                  </div>
                </button>
                <div className="flex gap-0.5 opacity-0 group-hover:opacity-100">
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      onStar(s.id);
                    }}
                    title={s.starred ? "Unstar" : "Star"}
                    className="hover:text-foreground text-muted-foreground rounded p-1"
                  >
                    {s.starred ? (
                      <StarIcon className="size-3 fill-current" />
                    ) : (
                      <StarOffIcon className="size-3" />
                    )}
                  </button>
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      onDelete(s.id);
                    }}
                    title="Delete"
                    className="hover:text-destructive text-muted-foreground rounded p-1"
                  >
                    <TrashIcon className="size-3" />
                  </button>
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </aside>
  );
}

function ChatPanel({
  messages,
  loading,
  prompt,
  onPromptChange,
  onSend,
}: {
  messages: SavedMessage[];
  loading: boolean;
  prompt: string;
  onPromptChange: (v: string) => void;
  onSend: () => void;
}) {
  return (
    <>
      <Card className="flex-1 overflow-auto">
        <CardContent className="flex flex-col gap-4 p-4">
          {messages.map((msg, i) => (
            <div key={i} className={`flex gap-3 ${msg.role === "user" ? "flex-row-reverse" : ""}`}>
              <div className="bg-muted flex h-8 w-8 shrink-0 items-center justify-center rounded-full">
                {msg.role === "user" ? (
                  <UserIcon className="h-4 w-4" />
                ) : (
                  <BotIcon className="h-4 w-4" />
                )}
              </div>
              <div
                className={cn(
                  "max-w-prose rounded-lg px-4 py-2 text-sm",
                  msg.role === "user"
                    ? "bg-primary text-primary-foreground"
                    : "bg-muted text-foreground"
                )}
              >
                {msg.content}
              </div>
            </div>
          ))}
          {loading && (
            <div className="flex gap-3">
              <div className="bg-muted flex h-8 w-8 shrink-0 items-center justify-center rounded-full">
                <BotIcon className="h-4 w-4" />
              </div>
              <div className="bg-muted text-muted-foreground animate-pulse rounded-lg px-4 py-2 text-sm">
                Thinking…
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      <div className="flex gap-2">
        <Textarea
          placeholder="Enter your prompt…"
          value={prompt}
          onChange={(e) => onPromptChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              onSend();
            }
          }}
          className="resize-none"
          rows={3}
        />
        <Button onClick={onSend} disabled={loading || !prompt.trim()} className="self-end">
          <SendIcon className="h-4 w-4" />
        </Button>
      </div>
    </>
  );
}

function BatchPanel({ model }: { model: string }) {
  const [input, setInput] = useState("");
  const [running, setRunning] = useState(false);
  const [results, setResults] = useState<BatchResult[]>([]);

  const inputs = useMemo(
    () =>
      input
        .split("\n")
        .map((line) => line.trim())
        .filter((line) => line.length > 0),
    [input]
  );

  const handleRun = async () => {
    if (inputs.length === 0) {
      toast.error("Provide at least one input line.");
      return;
    }
    setRunning(true);
    const collected: BatchResult[] = [];
    // Sequential execution so a batch run hits one model at a time —
    // simpler error model and easier on rate limits than fan-out.
    for (const line of inputs) {
      const res = await simulateBatchCall(line, model);
      collected.push(res);
      setResults([...collected]);
    }
    setRunning(false);
    toast.success(`Batch complete: ${collected.length} results`);
  };

  const handleExportCsv = () => {
    if (results.length === 0) {
      toast.error("Run the batch before exporting.");
      return;
    }
    downloadBlob(batchToCsv(results), "playground-batch.csv", "text/csv");
  };

  const handleExportJsonl = () => {
    if (results.length === 0) {
      toast.error("Run the batch before exporting.");
      return;
    }
    downloadBlob(batchToJsonl(results), "playground-batch.jsonl", "application/x-ndjson");
  };

  const handleCopyJson = async () => {
    if (results.length === 0) return;
    try {
      await navigator.clipboard.writeText(JSON.stringify(results, null, 2));
      toast.success("Results copied");
    } catch {
      toast.error("Could not copy");
    }
  };

  return (
    <div className="flex flex-1 flex-col gap-4">
      <div className="grid flex-1 grid-cols-1 gap-4 md:grid-cols-2">
        <div className="flex flex-col gap-2">
          <div className="flex items-center justify-between">
            <label className="text-sm font-medium">Inputs (one per line)</label>
            <span className="text-muted-foreground text-xs">{inputs.length} rows</span>
          </div>
          <Textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder='{"prompt": "What is 2+2?"}'
            rows={16}
            className="resize-none font-mono text-xs"
          />
          <div className="flex gap-2">
            <Button onClick={handleRun} disabled={running || inputs.length === 0}>
              <PlayIcon className="mr-1 size-3" />
              {running ? "Running…" : "Run all"}
            </Button>
            <Button variant="outline" onClick={handleExportCsv} disabled={results.length === 0}>
              <DownloadIcon className="mr-1 size-3" /> CSV
            </Button>
            <Button variant="outline" onClick={handleExportJsonl} disabled={results.length === 0}>
              <DownloadIcon className="mr-1 size-3" /> JSONL
            </Button>
            <Button variant="outline" onClick={handleCopyJson} disabled={results.length === 0}>
              <CopyIcon className="mr-1 size-3" /> Copy
            </Button>
          </div>
        </div>

        <div className="flex flex-col gap-2">
          <div className="flex items-center justify-between">
            <label className="text-sm font-medium">Results</label>
            <span className="text-muted-foreground text-xs">
              {results.filter((r) => r.ok).length}/{results.length} ok
            </span>
          </div>
          <div className="bg-muted/30 flex-1 overflow-auto rounded-md border">
            {results.length === 0 ? (
              <div className="text-muted-foreground flex h-full items-center justify-center p-6 text-center text-xs">
                Results will appear here after running.
              </div>
            ) : (
              <ul className="divide-border divide-y text-xs">
                {results.map((r, i) => (
                  <li key={i} className="p-2">
                    <div className="text-muted-foreground truncate">{r.input}</div>
                    <div className="mt-1 whitespace-pre-wrap">{r.ok ? r.output : r.error}</div>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

async function simulateBatchCall(input: string, model: string): Promise<BatchResult> {
  // Placeholder while playground has no real backend — keeps the UI
  // surface end-to-end so an operator can validate the batch flow
  // independent of the inference wiring. Real inference call lands
  // when the playground gateway exists.
  await new Promise<void>((resolve) => window.setTimeout(resolve, 100));
  return {
    input,
    output: `[${model}] simulated response for input length ${input.length}`,
    ok: true,
  };
}
