"use client";

import type { ReactNode } from "react";
import {
  BotIcon,
  SaveIcon,
  SendIcon,
  Share2Icon,
  StarIcon,
  StarOffIcon,
  TrashIcon,
  UserIcon,
} from "lucide-react";

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

import type { SavedMessage, SavedSessionIndexEntry } from "./saved-sessions";
import type { usePlayground } from "./use-playground";

export type PlaygroundScreenProps = ReturnType<typeof usePlayground> & {
  /** The batch tab, mounted only while it is selected so its state and hook live with it. */
  batch: ReactNode;
};

/** Prompt playground: saved-session sidebar, chat and batch tabs. */
export function PlaygroundScreen({
  models,
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
  onSend,
  onSave,
  onShare,
  onLoad,
  onNew,
  onDelete,
  onStar,
  batch,
}: PlaygroundScreenProps) {
  return (
    <div className="flex flex-1 gap-4 p-6">
      <Sidebar
        sessions={savedSessions}
        activeId={activeSavedId}
        onLoad={onLoad}
        onDelete={onDelete}
        onStar={onStar}
        onNew={onNew}
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
                {models.map((m) => (
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
            <Button size="sm" variant="outline" onClick={onSave}>
              <SaveIcon className="mr-1 size-3" /> Save
            </Button>
            <Button size="sm" variant="outline" onClick={onShare}>
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
            onSend={onSend}
          />
        ) : (
          batch
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
