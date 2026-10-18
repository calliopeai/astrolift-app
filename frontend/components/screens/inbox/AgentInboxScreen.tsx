"use client";

import { BotIcon, CheckCheckIcon, InboxIcon, PaperclipIcon, SendIcon } from "lucide-react";
import Link from "next/link";
import { useNow, useTranslations } from "next-intl";
import * as React from "react";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import {
  type AgentMessage,
  type AgentMessageReplyDraft,
  type AgentMessageThread,
  isAgentMessageKind,
} from "@/graphql/agents/agent-messages.types";
import { useFormatters } from "@/lib/i18n/formatters";
import { cn } from "@/lib/utils";

/**
 * The agent inbox (#2434): threads per agent run on the left, the open
 * thread on the right, answers inline. Pure: data and callbacks in.
 */
export interface AgentInboxScreenProps {
  threads: AgentMessageThread[];
  threadsLoading: boolean;
  threadsError: string | null;
  unreadCount: number;
  unreadOnly: boolean;
  onUnreadOnlyChange: (value: boolean) => void;
  hasMoreThreads: boolean;
  onLoadMoreThreads: () => void;
  selectedThreadId: string | null;
  onSelectThread: (threadId: string) => void;
  /** The open thread's messages, oldest first. */
  messages: AgentMessage[];
  messagesLoading: boolean;
  messagesError: string | null;
  hasEarlierMessages: boolean;
  onLoadEarlierMessages: () => void;
  onMarkThreadRead: (threadId: string) => void;
  onReply: (messageId: string, reply: AgentMessageReplyDraft) => void;
  replyingMessageId: string | null;
  replyError: string | null;
}

const KIND_STYLE: Record<string, string> = {
  update: "bg-info-bg text-info-fg border-info-border",
  question: "bg-warning-bg text-warning-fg border-warning-border",
  approval: "bg-warning-bg text-warning-fg border-warning-border",
  result: "bg-success-bg text-success-fg border-success-border",
  alert: "bg-danger-bg text-danger-fg border-danger-border",
};

const ROUTES = new Set(["live_run", "gate", "woke", "queued"]);

export function AgentInboxScreen(props: AgentInboxScreenProps) {
  const t = useTranslations("agentInbox");
  const { threads, threadsLoading, threadsError, selectedThreadId } = props;
  const selected = threads.find((thread) => thread.threadId === selectedThreadId) ?? null;

  return (
    <PageShell
      title={t("title")}
      description={t("description", { count: props.unreadCount })}
      actions={
        <div className="flex items-center gap-2">
          <Checkbox
            id="agent-inbox-unread"
            checked={props.unreadOnly}
            onCheckedChange={(value) => props.onUnreadOnlyChange(value === true)}
          />
          <Label htmlFor="agent-inbox-unread" className="text-sm">
            {t("unreadOnly")}
          </Label>
        </div>
      }
    >
      {threadsError ? (
        <ErrorNote message={threadsError} />
      ) : threadsLoading && threads.length === 0 ? (
        <div className="grid gap-4 md:grid-cols-[18rem_1fr]">
          <Skeleton className="h-64 w-full" />
          <Skeleton className="h-64 w-full" />
        </div>
      ) : threads.length === 0 ? (
        <EmptyState
          icon={<InboxIcon className="size-6" aria-hidden />}
          title={props.unreadOnly ? t("emptyUnreadTitle") : t("emptyTitle")}
          description={t("emptyDescription")}
        />
      ) : (
        <div className="grid min-h-0 gap-4 md:grid-cols-[18rem_1fr]">
          <ThreadList {...props} />
          {selected ? (
            <ThreadView {...props} thread={selected} />
          ) : (
            <div className="text-muted-foreground flex items-center justify-center rounded-md border p-8 text-sm">
              {t("pickThread")}
            </div>
          )}
        </div>
      )}
    </PageShell>
  );
}

function ErrorNote({ message }: { message: string }) {
  return (
    <div
      role="alert"
      className="bg-danger-bg text-danger-fg border-danger-border rounded-md border p-3 text-sm"
    >
      {message}
    </div>
  );
}

function KindBadge({ kind }: { kind: string }) {
  const t = useTranslations("agentInbox.kind");
  return (
    <Badge variant="outline" className={cn("text-2xs", KIND_STYLE[kind])}>
      {isAgentMessageKind(kind) ? t(kind) : kind}
    </Badge>
  );
}

function ThreadList({
  threads,
  selectedThreadId,
  onSelectThread,
  hasMoreThreads,
  onLoadMoreThreads,
  threadsLoading,
}: AgentInboxScreenProps) {
  const t = useTranslations("agentInbox");
  const fmt = useFormatters();
  const now = useNow();
  return (
    <nav aria-label={t("threads")} className="flex min-w-0 flex-col gap-2">
      <ul className="flex max-h-[70vh] flex-col divide-y overflow-y-auto rounded-md border">
        {threads.map((thread) => {
          const active = thread.threadId === selectedThreadId;
          return (
            <li key={thread.threadId}>
              <button
                type="button"
                onClick={() => onSelectThread(thread.threadId)}
                aria-current={active ? "true" : undefined}
                className={cn(
                  "hover:bg-muted/60 flex w-full min-w-0 flex-col gap-1 p-3 text-left text-sm",
                  active && "bg-muted",
                  thread.unreadCount > 0 && "border-l-primary border-l-2"
                )}
              >
                <span className="flex min-w-0 items-center justify-between gap-2">
                  <span className="flex min-w-0 items-center gap-1.5 font-medium">
                    <BotIcon className="size-3.5 shrink-0" aria-hidden />
                    <span className="truncate">{thread.agentName ?? t("unknownAgent")}</span>
                  </span>
                  {thread.unreadCount > 0 && (
                    <Badge className="text-2xs h-4 min-w-4 px-1 tabular-nums">
                      {thread.unreadCount > 99 ? "99+" : thread.unreadCount}
                    </Badge>
                  )}
                </span>
                <span className="text-muted-foreground line-clamp-1">
                  {thread.lastMessage.title || thread.lastMessage.body}
                </span>
                <span className="text-muted-foreground text-2xs flex items-center gap-2">
                  <KindBadge kind={thread.lastMessage.kind} />
                  {fmt.formatRelativeTime(thread.lastMessageAt, now)}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
      {hasMoreThreads && (
        <Button variant="outline" size="sm" onClick={onLoadMoreThreads} disabled={threadsLoading}>
          {t("more")}
        </Button>
      )}
    </nav>
  );
}

function ThreadView({
  thread,
  messages,
  messagesLoading,
  messagesError,
  hasEarlierMessages,
  onLoadEarlierMessages,
  onMarkThreadRead,
  onReply,
  replyingMessageId,
  replyError,
}: AgentInboxScreenProps & { thread: AgentMessageThread }) {
  const t = useTranslations("agentInbox");
  const last = messages[messages.length - 1];
  return (
    <section aria-label={t("thread")} className="flex min-w-0 flex-col gap-3">
      <header className="flex min-w-0 flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <h2 className="truncate font-semibold">{thread.agentName ?? t("unknownAgent")}</h2>
          {thread.taskId && (
            <Link
              href={`/agents/runs/${thread.taskId}`}
              className="text-muted-foreground hover:text-foreground text-xs"
            >
              {t("openRun")}
            </Link>
          )}
        </div>
        {thread.unreadCount > 0 && (
          <Button variant="ghost" size="sm" onClick={() => onMarkThreadRead(thread.threadId)}>
            <CheckCheckIcon className="size-3.5" aria-hidden />
            {t("markRead")}
          </Button>
        )}
      </header>
      {messagesError && <ErrorNote message={messagesError} />}
      {hasEarlierMessages && (
        <Button
          variant="outline"
          size="sm"
          onClick={onLoadEarlierMessages}
          disabled={messagesLoading}
        >
          {t("earlier")}
        </Button>
      )}
      {messagesLoading && messages.length === 0 ? (
        <Skeleton className="h-40 w-full" />
      ) : (
        <ol className="flex max-h-[60vh] flex-col gap-3 overflow-y-auto">
          {messages.map((message) => (
            <li key={message.id}>
              <MessageCard
                message={message}
                onReply={onReply}
                replying={replyingMessageId === message.id}
              />
            </li>
          ))}
        </ol>
      )}
      {replyError && <ErrorNote message={replyError} />}
      {last && !last.answerable && (
        <Composer
          label={t("replyToAgent")}
          busy={replyingMessageId === last.id}
          onSend={(body) => onReply(last.id, { body })}
        />
      )}
    </section>
  );
}

function MessageCard({
  message,
  onReply,
  replying,
}: {
  message: AgentMessage;
  onReply: AgentInboxScreenProps["onReply"];
  replying: boolean;
}) {
  const t = useTranslations("agentInbox");
  const fmt = useFormatters();
  const now = useNow();
  return (
    <article
      className={cn(
        "flex min-w-0 flex-col gap-2 rounded-md border p-3 text-sm",
        !message.readAt && "border-l-primary border-l-2"
      )}
    >
      <div className="flex min-w-0 flex-wrap items-center gap-2">
        <KindBadge kind={message.kind} />
        {message.title && <span className="min-w-0 truncate font-medium">{message.title}</span>}
        <span className="text-muted-foreground text-2xs ml-auto">
          {fmt.formatRelativeTime(message.createdAt, now)}
        </span>
      </div>
      <p className="break-words whitespace-pre-wrap">{message.body}</p>
      {message.attachments.length > 0 && (
        <ul className="flex flex-wrap gap-2">
          {message.attachments.map((attachment) => (
            <li key={`${attachment.kind}:${attachment.ref}`}>
              <AttachmentChip
                kind={attachment.kind}
                reference={attachment.ref}
                label={attachment.label}
              />
            </li>
          ))}
        </ul>
      )}
      {message.replies.length > 0 && (
        <ul className="border-muted flex flex-col gap-1 border-l-2 pl-3">
          {message.replies.map((reply) => (
            <li key={reply.id} className="text-muted-foreground text-xs">
              <span className="text-foreground font-medium">{reply.authorLabel}</span>{" "}
              {[reply.decision, ...reply.choices, reply.body].filter(Boolean).join(" · ")}
              {ROUTES.has(reply.route) && (
                <span className="ml-2">({t(`route.${reply.route as "live_run"}`)})</span>
              )}
            </li>
          ))}
        </ul>
      )}
      {message.answerable && <Answer message={message} onReply={onReply} busy={replying} />}
      {message.expiresAt && message.answerable && (
        <p className="text-muted-foreground text-2xs">
          {t("expires", { when: fmt.formatRelativeTime(message.expiresAt, now) })}
        </p>
      )}
    </article>
  );
}

function AttachmentChip({
  kind,
  reference,
  label,
}: {
  kind: string;
  reference: string;
  label?: string | null;
}) {
  const text = label || reference;
  const chip = (
    <span className="bg-muted inline-flex max-w-64 items-center gap-1 rounded px-2 py-0.5 text-xs">
      <PaperclipIcon className="size-3 shrink-0" aria-hidden />
      <span className="truncate">{text}</span>
    </span>
  );
  return kind === "url" ? (
    <Link href={reference} prefetch={false}>
      {chip}
    </Link>
  ) : (
    chip
  );
}

function Answer({
  message,
  onReply,
  busy,
}: {
  message: AgentMessage;
  onReply: AgentInboxScreenProps["onReply"];
  busy: boolean;
}) {
  const t = useTranslations("agentInbox");
  if (message.kind === "approval") {
    return (
      <Composer
        label={t("reason")}
        busy={busy}
        optional
        actions={(body) => (
          <>
            <Button
              size="sm"
              disabled={busy}
              onClick={() => onReply(message.id, { decision: "allow", body: body || undefined })}
            >
              {t("allow")}
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={() => onReply(message.id, { decision: "deny", body: body || undefined })}
            >
              {t("deny")}
            </Button>
          </>
        )}
      />
    );
  }
  return (
    <div className="flex flex-col gap-2">
      {message.options.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {message.options.map((option) => (
            <Button
              key={option.id}
              size="sm"
              variant="outline"
              disabled={busy}
              title={option.description ?? undefined}
              onClick={() => onReply(message.id, { choices: [option.id] })}
            >
              {option.label}
            </Button>
          ))}
        </div>
      )}
      <Composer label={t("answer")} busy={busy} onSend={(body) => onReply(message.id, { body })} />
    </div>
  );
}

function Composer({
  label,
  busy,
  optional = false,
  onSend,
  actions,
}: {
  label: string;
  busy: boolean;
  optional?: boolean;
  onSend?: (body: string) => void;
  actions?: (body: string) => React.ReactNode;
}) {
  const t = useTranslations("agentInbox");
  const [body, setBody] = React.useState("");
  const id = React.useId();
  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={id} className="text-xs">
        {label}
      </Label>
      <Textarea
        id={id}
        value={body}
        rows={2}
        maxLength={4000}
        onChange={(event) => setBody(event.target.value)}
      />
      <div className="flex justify-end gap-2">
        {actions?.(body.trim())}
        {onSend && (
          <Button
            size="sm"
            disabled={busy || (!optional && !body.trim())}
            onClick={() => {
              onSend(body.trim());
              setBody("");
            }}
          >
            <SendIcon className="size-3.5" aria-hidden />
            {t("send")}
          </Button>
        )}
      </div>
    </div>
  );
}
