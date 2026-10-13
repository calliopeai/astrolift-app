"use client";

import { AlertTriangleIcon, LifeBuoyIcon } from "lucide-react";
import { useTranslations } from "next-intl";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { useFormatters } from "@/lib/i18n/formatters";

import type { SupportErrorCode, SupportState, SupportTicket } from "./use-support";

export type SupportScreenProps = Omit<SupportState, "onCreate" | "onSendReply"> & {
  onCreate: () => void;
  onSendReply: () => void;
};

const SEVERITIES = [1, 2, 3, 4] as const;
const STATUSES = [
  "new",
  "triaged",
  "accepted",
  "backlog",
  "declined",
  "fixed_pending_release",
  "released",
] as const;
const EVENTS = [
  "created",
  "status_changed",
  "severity_changed",
  "commented",
  "support_replied",
] as const;
const ERRORS: Record<SupportErrorCode, string> = {
  SUPPORT_COVERAGE_REQUIRED: "coverage",
  SUPPORT_REQUEST_INVALID: "invalid",
  PERMISSION_DENIED: "denied",
  NOT_FOUND: "notFound",
  SUPPORT_UNAVAILABLE: "unavailableNow",
};

function useLabels() {
  const t = useTranslations("support");
  return {
    t,
    status: (value: string) =>
      (STATUSES as readonly string[]).includes(value) ? t(`status.${value}`) : value,
    error: (code: SupportErrorCode) => t(`errors.${ERRORS[code]}`),
  };
}

function TicketRow({ ticket, onOpen }: { ticket: SupportTicket; onOpen: () => void }) {
  const { t, status } = useLabels();
  const fmt = useFormatters();
  return (
    <li className="border-border/60 border-b last:border-b-0">
      <button
        type="button"
        onClick={onOpen}
        className="hover:bg-muted/40 focus-visible:ring-ring flex w-full min-w-0 flex-wrap items-center gap-x-3 gap-y-1 px-4 py-3 text-left focus-visible:ring-2 focus-visible:outline-none"
      >
        <span className="min-w-0 flex-1 font-medium [overflow-wrap:anywhere]">{ticket.title}</span>
        <Badge variant="outline">{status(ticket.status)}</Badge>
        <span className="text-muted-foreground font-mono text-xs">
          {t("severityShort", { level: ticket.severity })}
        </span>
        <span className="text-muted-foreground text-xs">
          {t("updated", { when: fmt.formatRelativeTime(ticket.updated_at) })}
        </span>
      </button>
    </li>
  );
}

function NewCase(p: SupportScreenProps) {
  const { t, error } = useLabels();
  const ready = p.draft.title.trim() !== "" && p.draft.body.trim() !== "" && !p.creating;
  return (
    <Card className="space-y-3 p-4">
      <h2 className="font-semibold">{t("newCase")}</h2>
      <div className="space-y-1.5">
        <Label htmlFor="support-title">{t("caseTitle")}</Label>
        <Input
          id="support-title"
          value={p.draft.title}
          maxLength={300}
          onChange={(e) => p.onDraft({ ...p.draft, title: e.target.value })}
        />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="support-body">{t("caseBody")}</Label>
        <Textarea
          id="support-body"
          rows={4}
          value={p.draft.body}
          onChange={(e) => p.onDraft({ ...p.draft, body: e.target.value })}
        />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="support-severity">{t("severity")}</Label>
        <select
          id="support-severity"
          className="bg-background h-9 w-full max-w-sm rounded-md border px-2"
          value={p.draft.severity}
          onChange={(e) => p.onDraft({ ...p.draft, severity: Number(e.target.value) })}
        >
          {SEVERITIES.map((level) => (
            <option key={level} value={level}>
              {t(`sev${level}`)}
            </option>
          ))}
        </select>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <Button onClick={p.onCreate} disabled={!ready}>
          {p.creating ? t("submitting") : t("submit")}
        </Button>
        {p.createdId ? (
          <p role="status" className="text-sm">
            {t("created")} <code className="font-mono text-xs">{p.createdId}</code>
          </p>
        ) : null}
        {p.createError ? (
          <p role="alert" className="text-danger text-sm">
            {error(p.createError)}
          </p>
        ) : null}
      </div>
    </Card>
  );
}

function CaseSheet(p: SupportScreenProps) {
  const { t, status, error } = useLabels();
  const fmt = useFormatters();
  const ticket = p.selected;
  return (
    <Sheet open={ticket !== null} onOpenChange={(next) => (next ? null : p.onOpen(null))}>
      <SheetContent className="flex w-full flex-col gap-4 overflow-y-auto sm:max-w-lg">
        {ticket ? (
          <>
            <SheetHeader>
              <SheetTitle className="[overflow-wrap:anywhere]">{ticket.title}</SheetTitle>
              <SheetDescription className="flex flex-wrap items-center gap-2">
                <Badge variant="outline">{status(ticket.status)}</Badge>
                <span>{t(`sev${ticket.severity as 1 | 2 | 3 | 4}`)}</span>
              </SheetDescription>
            </SheetHeader>
            <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 px-4 text-sm">
              <dt className="text-muted-foreground">{t("caseId")}</dt>
              <dd className="font-mono text-xs [overflow-wrap:anywhere]">{ticket.id}</dd>
              <dt className="text-muted-foreground">{t("opened")}</dt>
              <dd>{fmt.formatDateTime(ticket.created_at)}</dd>
              {ticket.coverage_summary?.window ? (
                <>
                  <dt className="text-muted-foreground">{t("coverage")}</dt>
                  <dd>
                    {ticket.coverage_summary.window}
                    {ticket.coverage_summary.continuous_for_severity ? ` · ${t("continuous")}` : ""}
                  </dd>
                </>
              ) : null}
            </dl>
            <section className="space-y-2 px-4" aria-label={t("thread")}>
              <h3 className="text-sm font-semibold">{t("thread")}</h3>
              <p className="text-sm [overflow-wrap:anywhere] whitespace-pre-wrap">{ticket.body}</p>
              {p.detailLoading && ticket.events.length === 0 ? (
                <Skeleton className="h-16 w-full" />
              ) : null}
              <ol className="space-y-2">
                {ticket.events.map((event) => (
                  <li key={event.id} className="border-border/60 border-l-2 pl-3 text-sm">
                    <p className="text-muted-foreground text-xs">
                      {(EVENTS as readonly string[]).includes(event.action)
                        ? t(`event.${event.action}`, {
                            author: event.author ?? t("supportTeam"),
                            to: event.to ? status(event.to) : "",
                          })
                        : event.action}{" "}
                      · {fmt.formatDateTime(event.at)}
                    </p>
                    {event.action === "commented" || event.action === "support_replied" ? (
                      <p className="[overflow-wrap:anywhere] whitespace-pre-wrap">{event.detail}</p>
                    ) : null}
                  </li>
                ))}
              </ol>
              {p.detailError ? (
                <p role="alert" className="text-danger text-sm">
                  {error(p.detailError)}
                </p>
              ) : null}
            </section>
            <div className="space-y-2 px-4 pb-4">
              <Label htmlFor="support-reply">{t("reply")}</Label>
              <Textarea
                id="support-reply"
                rows={3}
                value={p.reply}
                onChange={(e) => p.onReply(e.target.value)}
              />
              <div className="flex flex-wrap items-center gap-3">
                <Button onClick={p.onSendReply} disabled={!p.reply.trim() || p.replying}>
                  {p.replying ? t("sending") : t("sendReply")}
                </Button>
                {p.replyError ? (
                  <p role="alert" className="text-danger text-sm">
                    {error(p.replyError)}
                  </p>
                ) : null}
              </div>
            </div>
          </>
        ) : null}
      </SheetContent>
    </Sheet>
  );
}

/** Open a support case for this install and follow it (#1809). */
export function SupportScreen(p: SupportScreenProps) {
  const { t, error } = useLabels();
  if (p.unavailable) {
    return (
      <PageShell title={t("title")}>
        <EmptyState
          icon={<LifeBuoyIcon className="size-5" />}
          title={t("unavailable")}
          description={t("unavailableHelp")}
        />
      </PageShell>
    );
  }
  return (
    <PageShell title={t("title")} description={t("description")}>
      <NewCase {...p} />
      <section className="space-y-2" aria-label={t("cases")}>
        <h2 className="font-semibold">{t("cases")}</h2>
        {p.loading && p.tickets.length === 0 ? (
          <div className="space-y-2">
            <Skeleton className="h-12 w-full" />
            <Skeleton className="h-12 w-full" />
          </div>
        ) : p.error ? (
          <EmptyState
            icon={<AlertTriangleIcon className="text-danger size-5" />}
            title={t("loadError")}
            description={error(p.error)}
            secondary={
              <Button size="sm" variant="outline" onClick={p.onRetry}>
                {t("retry")}
              </Button>
            }
          />
        ) : p.tickets.length === 0 ? (
          <EmptyState
            icon={<LifeBuoyIcon className="size-5" />}
            title={t("empty")}
            description={t("emptyHelp")}
          />
        ) : (
          <Card className="p-0">
            <ul>
              {p.tickets.map((ticket) => (
                <TicketRow key={ticket.id} ticket={ticket} onOpen={() => p.onOpen(ticket.id)} />
              ))}
            </ul>
          </Card>
        )}
      </section>
      <CaseSheet {...p} />
    </PageShell>
  );
}
