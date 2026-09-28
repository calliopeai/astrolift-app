"use client";

import {
  AlertTriangleIcon,
  BookOpenIcon,
  CheckCircle2Icon,
  ChevronDownIcon,
  ChevronRightIcon,
  CopyIcon,
  KeyRoundIcon,
  PauseIcon,
  PlayIcon,
  PlusIcon,
  SendIcon,
  Trash2Icon,
  WebhookIcon,
  XCircleIcon,
} from "lucide-react";
import Link from "next/link";
import * as React from "react";
import { toast } from "sonner";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { PageShell } from "@/components/PageShell";
import { DataTable, type Column } from "@/components/data-table";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { DefinitionList } from "@/components/ui/definition-list";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Textarea } from "@/components/ui/textarea";
import type {
  AstroliftWebhookSubscription,
  AstroliftWebhookTestResult,
  WebhookFormat,
} from "@/graphql/operations/operations.types";
import { DOC_LINKS } from "@/lib/docs/urls";
import { useFormatters } from "@/lib/i18n/formatters";

import type { WebhooksData } from "./use-webhooks";
import { SNIPPETS, SNIPPET_LABELS, type SnippetLanguage } from "./verification-snippets";

const SUGGESTED_EVENTS = [
  "APP_REGISTERED",
  "DEPLOY_STARTED",
  "DEPLOY_SUCCEEDED",
  "DEPLOY_FAILED",
  "PREVIEW_CREATED",
  "PREVIEW_TORN_DOWN",
];

const FORMAT_OPTIONS: { value: WebhookFormat; label: string; hint: string }[] = [
  {
    value: "generic",
    label: "Generic (Astrolift envelope)",
    hint: "Raw event JSON — best for collectors and your own services.",
  },
  {
    value: "slack",
    label: "Slack incoming webhook",
    hint: "Adapts to Slack's text + attachments shape so messages render in-channel.",
  },
  {
    value: "discord",
    label: "Discord webhook",
    hint: "Adapts to Discord's content + embeds shape.",
  },
];

export type WebhooksScreenProps = WebhooksData & {
  /** Set on the app/agent-scoped tab; unset on the platform-wide surface. */
  appSlug?: string;
  tabs?: React.ReactNode;
  /** The expanded subscription's detail panel. The route fills it with a
   *  container so its deliveries walk runs only while it is shown. */
  renderDetail: (
    subscription: AstroliftWebhookSubscription,
    onClose: () => void
  ) => React.ReactNode;
};

export function WebhooksScreen({
  appSlug,
  tabs,
  renderDetail,
  table,
  creating,
  rotating,
  firing,
  deleting,
  reveal,
  dismissReveal,
  copySecret,
  testResult,
  dismissTestResult,
  onCreate,
  onDelete,
  onToggleActive,
  onFormatChange,
  onRotate,
  onTestFire,
}: WebhooksScreenProps) {
  const fmt = useFormatters();
  const [open, setOpen] = React.useState(false);
  const [rotateTarget, setRotateTarget] = React.useState<AstroliftWebhookSubscription | null>(null);
  const [testTarget, setTestTarget] = React.useState<AstroliftWebhookSubscription | null>(null);
  const [deleteTarget, setDeleteTarget] = React.useState<AstroliftWebhookSubscription | null>(null);
  const [expandedId, setExpandedId] = React.useState<string | null>(null);

  const [url, setUrl] = React.useState("");
  const [eventsRaw, setEventsRaw] = React.useState(SUGGESTED_EVENTS.join("\n"));
  const [format, setFormat] = React.useState<WebhookFormat>("generic");

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (await onCreate({ url, eventsRaw, format })) {
      setOpen(false);
      setUrl("");
      setFormat("generic");
    }
  }

  // The expanded subscription is resolved against the page in hand, so
  // paging away (or searching past it) closes the panel rather than
  // leaving it describing a row that is no longer on screen.
  const expanded = table.rows.find((s) => s.id === expandedId) ?? null;

  const columns: Column<AstroliftWebhookSubscription>[] = [
    {
      id: "expand",
      header: <span className="sr-only">Details</span>,
      width: "w-8",
      cellClassName: "p-2",
      cell: (s) => (
        <Button
          variant="ghost"
          size="icon"
          className="size-7"
          onClick={() => setExpandedId((prev) => (prev === s.id ? null : s.id))}
          aria-label={expandedId === s.id ? "Collapse" : "Expand"}
          aria-expanded={expandedId === s.id}
        >
          {expandedId === s.id ? (
            <ChevronDownIcon className="size-4" />
          ) : (
            <ChevronRightIcon className="size-4" />
          )}
        </Button>
      ),
    },
    {
      id: "url",
      header: "URL",
      cellClassName: "max-w-xs truncate font-mono text-xs",
      cell: (s) =>
        appSlug ? (
          s.url
        ) : (
          <Link
            href={`/webhooks/${s.id}`}
            className="hover:text-[var(--brand-primary)] hover:underline"
          >
            {s.url}
          </Link>
        ),
    },
    {
      id: "events",
      header: "Events",
      cell: (s) => (
        <div className="flex flex-wrap gap-1">
          {s.events.slice(0, 3).map((e) => (
            <Badge key={e} variant="outline" className="text-xs">
              {e}
            </Badge>
          ))}
          {s.events.length > 3 && (
            <Badge variant="secondary" className="text-xs">
              +{s.events.length - 3}
            </Badge>
          )}
        </div>
      ),
    },
    {
      id: "format",
      header: "Format",
      cell: (s) => (
        <Can
          permission="webhook.update"
          fallback={
            <Badge variant="outline" className="text-xs capitalize">
              {s.format}
            </Badge>
          }
        >
          <Select value={s.format} onValueChange={(v) => onFormatChange(s, v as WebhookFormat)}>
            <SelectTrigger size="sm" className="h-7 w-32 text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {FORMAT_OPTIONS.map((o) => (
                <SelectItem key={o.value} value={o.value}>
                  {o.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Can>
      ),
    },
    {
      id: "lastDelivery",
      header: "Last delivery",
      cellClassName: "text-muted-foreground text-sm",
      cell: (s) =>
        s.lastDeliveryAt
          ? `${fmt.formatDateTime(s.lastDeliveryAt)} (${s.lastResponseStatus ?? "?"})`
          : "never",
    },
    {
      id: "failures",
      header: "Failures",
      cellClassName: "font-mono text-xs",
      cell: (s) =>
        s.failureCount > 0 ? (
          <span className="text-destructive inline-flex items-center gap-1">
            <AlertTriangleIcon className="size-3" />
            {s.failureCount}
          </span>
        ) : (
          "0"
        ),
    },
    {
      id: "status",
      header: "Status",
      cell: (s) => (
        <Badge variant={s.isActive ? "secondary" : "outline"}>
          {s.isActive ? "active" : "paused"}
        </Badge>
      ),
    },
    {
      id: "actions",
      header: "Actions",
      align: "right",
      cell: (s) => (
        <div className="flex justify-end gap-1">
          <Can permission="webhook.update">
            <Button
              size="sm"
              variant="ghost"
              title={s.isActive ? "Pause" : "Resume"}
              onClick={() => onToggleActive(s)}
            >
              {s.isActive ? <PauseIcon className="size-4" /> : <PlayIcon className="size-4" />}
              <span className="sr-only">{s.isActive ? "Pause" : "Resume"}</span>
            </Button>
            <Button
              size="sm"
              variant="ghost"
              title="Send test event"
              disabled={firing || !s.isActive}
              onClick={() => setTestTarget(s)}
            >
              <SendIcon className="size-4" />
              <span className="sr-only">Send test</span>
            </Button>
            <Button
              size="sm"
              variant="ghost"
              title="Rotate secret"
              disabled={rotating}
              onClick={() => setRotateTarget(s)}
            >
              <KeyRoundIcon className="size-4" />
              <span className="sr-only">Rotate secret</span>
            </Button>
          </Can>
          <Can permission="webhook.delete">
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setDeleteTarget(s)}
              disabled={deleting}
            >
              <Trash2Icon className="size-4" />
              <span className="sr-only">Delete</span>
            </Button>
          </Can>
        </div>
      ),
    },
  ];

  return (
    <PageShell
      title="Webhooks"
      description={
        appSlug ? (
          <span className="text-muted-foreground font-mono text-xs">
            Outbound HTTP delivery for {appSlug}&apos;s event stream. Each subscription&apos;s
            secret is used to HMAC-sign every payload.
          </span>
        ) : (
          "Outbound HTTP delivery for the platform's event log. Each subscription's secret is used to HMAC-sign every payload."
        )
      }
      actions={
        <>
          <Button asChild size="sm" variant="outline">
            <Link href={DOC_LINKS.webhooks}>
              <BookOpenIcon className="size-4" />
              Learn more
            </Link>
          </Button>
          <Can permission="webhook.create">
            <Button onClick={() => setOpen(true)}>
              <PlusIcon className="size-4" />
              New webhook
            </Button>
          </Can>
        </>
      }
    >
      {tabs}
      {reveal && (
        <Card className="border-success-border bg-success/5">
          <CardContent className="flex flex-col gap-3 p-4">
            <div className="flex items-center gap-2">
              <CheckCircle2Icon className="text-success-fg size-4" />
              <p className="text-sm font-medium">
                Secret for <span className="font-mono">{reveal.subscription.url}</span> ready
              </p>
            </div>
            <p className="text-muted-foreground text-xs">
              Save the HMAC secret below — it&apos;s never shown again. We store only its SHA-256.
              The previous secret stays valid for the rotation grace window.
            </p>
            <div className="flex items-center gap-2">
              <code className="bg-background flex-1 rounded-md border px-3 py-2 font-mono text-xs break-all">
                {reveal.plaintextSecret}
              </code>
              <Button size="sm" variant="outline" onClick={copySecret}>
                <CopyIcon className="size-4" />
                Copy
              </Button>
            </div>
            <div className="flex justify-end">
              <Button size="sm" variant="ghost" onClick={dismissReveal}>
                Dismiss
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {testResult && <TestResultCard result={testResult} onDismiss={dismissTestResult} />}

      <DataTable
        label="Webhook subscriptions"
        controller={table}
        columns={columns}
        getRowId={(s) => s.id}
        rowClassName={(s) =>
          s.isActive ? undefined : "text-muted-foreground line-through opacity-60"
        }
        searchPlaceholder="Search webhooks…"
        empty={{
          icon: <WebhookIcon className="size-5" />,
          title: "No webhook subscriptions",
          description:
            "Subscribe an external system (Zentinelle, Slack relay, custom collector) to platform events.",
        }}
        emptyFiltered={{
          title: "No matching webhooks",
          description:
            "No subscription matches that search. The server matches the delivery URL, not the event list.",
        }}
      />

      {expanded && (
        // Keyed by id so expanding a different subscription starts its own
        // delivery walk rather than inheriting the previous one's search.
        <React.Fragment key={expanded.id}>
          {renderDetail(expanded, () => setExpandedId(null))}
        </React.Fragment>
      )}

      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent className="flex flex-col">
          <SheetHeader>
            <SheetTitle>New webhook</SheetTitle>
            <SheetDescription>
              The HMAC secret is shown once at creation; we store only the SHA-256. Use it to verify
              the X-Astrolift-Signature header on incoming deliveries.
            </SheetDescription>
          </SheetHeader>
          <form onSubmit={submit} className="flex flex-1 flex-col gap-4 px-4 pb-4">
            <div className="space-y-2">
              <Label htmlFor="webhook-url">URL</Label>
              <Input
                id="webhook-url"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                type="url"
                required
                placeholder="https://collector.example/astrolift"
                autoFocus
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="webhook-format">Format</Label>
              <Select value={format} onValueChange={(v) => setFormat(v as WebhookFormat)}>
                <SelectTrigger id="webhook-format">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {FORMAT_OPTIONS.map((o) => (
                    <SelectItem key={o.value} value={o.value}>
                      {o.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-muted-foreground text-xs">
                {FORMAT_OPTIONS.find((o) => o.value === format)?.hint}
              </p>
            </div>
            <div className="space-y-2">
              <Label htmlFor="events">Events</Label>
              <Textarea
                id="events"
                value={eventsRaw}
                onChange={(e) => setEventsRaw(e.target.value)}
                rows={8}
                className="font-mono text-xs"
              />
              <p className="text-muted-foreground text-xs">
                One per line, or comma/space separated. <code>*</code> matches all events.
              </p>
            </div>
            <VerificationSnippetDisclosure />
            <SheetFooter className="mt-auto flex-row justify-end gap-2 px-0">
              <Button type="button" variant="outline" onClick={() => setOpen(false)}>
                Cancel
              </Button>
              <Button type="submit" disabled={creating || !url}>
                {creating ? "Creating…" : "Create webhook"}
              </Button>
            </SheetFooter>
          </form>
        </SheetContent>
      </Sheet>

      <ConfirmDialog
        open={deleteTarget !== null}
        onOpenChange={(next) => {
          if (!next) setDeleteTarget(null);
        }}
        title="Delete webhook?"
        description={
          deleteTarget
            ? `${deleteTarget.url} will stop receiving events immediately. Past deliveries stay in the audit log.`
            : undefined
        }
        confirmLabel="Delete webhook"
        destructive
        onConfirm={async () => {
          if (deleteTarget) await onDelete(deleteTarget);
        }}
      />

      <ConfirmDialog
        open={rotateTarget !== null}
        onOpenChange={(next) => {
          if (!next) setRotateTarget(null);
        }}
        title="Rotate webhook secret?"
        description={
          rotateTarget
            ? `Generate a new HMAC secret for ${rotateTarget.url}. The previous secret stays valid for the configured grace window so subscribers can roll out without dropping deliveries. The new plaintext is shown exactly once.`
            : undefined
        }
        confirmLabel="Rotate secret"
        onConfirm={async () => {
          if (rotateTarget) await onRotate(rotateTarget);
        }}
      />

      <ConfirmDialog
        open={testTarget !== null}
        onOpenChange={(next) => {
          if (!next) setTestTarget(null);
        }}
        title="Send a test event?"
        description={
          testTarget
            ? `POST a synthetic webhook.test event to ${testTarget.url}. No retries, no auto-disable bookkeeping — the response is shown inline.`
            : undefined
        }
        confirmLabel="Send test"
        onConfirm={async () => {
          if (testTarget) await onTestFire(testTarget);
        }}
      />
    </PageShell>
  );
}

function TestResultCard({
  result,
  onDismiss,
}: {
  result: AstroliftWebhookTestResult;
  onDismiss: () => void;
}) {
  const ok =
    result.delivered && result.statusCode && result.statusCode >= 200 && result.statusCode < 300;
  const border = ok ? "border-success-border bg-success/5" : "border-warning-border bg-warning/5";
  return (
    <Card className={border}>
      <CardContent className="flex flex-col gap-3 p-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 text-sm font-medium">
            {ok ? (
              <CheckCircle2Icon className="text-success-fg size-4" />
            ) : (
              <XCircleIcon className="text-warning-fg size-4" />
            )}
            Test delivery to <span className="font-mono">{result.url}</span>
          </div>
          <Button size="sm" variant="ghost" onClick={onDismiss}>
            Dismiss
          </Button>
        </div>
        <DefinitionList
          orientation="stack"
          className="grid grid-cols-3 gap-3 text-xs"
          items={[
            { term: "Status", description: result.statusCode ?? "—" },
            { term: "Latency", description: `${result.durationMs}ms` },
            {
              term: "Delivery ID",
              description: <span className="font-mono">{result.deliveryId || "—"}</span>,
            },
          ]}
        />
        {result.error && (
          <div className="bg-background rounded border p-2 font-mono text-xs whitespace-pre-wrap">
            {result.error}
          </div>
        )}
        {result.responseBodyExcerpt && (
          <div className="bg-background max-h-40 overflow-auto rounded border p-2 font-mono text-xs whitespace-pre-wrap">
            {result.responseBodyExcerpt}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function VerificationSnippetDisclosure() {
  const [lang, setLang] = React.useState<SnippetLanguage>("node");
  const langs: SnippetLanguage[] = ["node", "python", "go"];

  function copy() {
    navigator.clipboard.writeText(SNIPPETS[lang]);
    toast.success("Snippet copied");
  }

  return (
    <Collapsible>
      <CollapsibleTrigger asChild>
        <Button variant="outline" size="sm" type="button" className="w-full justify-start">
          <ChevronRightIcon className="size-4" />
          Show verification snippet
        </Button>
      </CollapsibleTrigger>
      <CollapsibleContent className="space-y-2 pt-2">
        <div className="flex items-center gap-2">
          <div className="flex rounded-md border">
            {langs.map((l) => (
              <button
                key={l}
                type="button"
                onClick={() => setLang(l)}
                className={`px-3 py-1 text-xs ${
                  lang === l
                    ? "bg-foreground text-background"
                    : "text-muted-foreground hover:text-foreground"
                }`}
              >
                {SNIPPET_LABELS[l]}
              </button>
            ))}
          </div>
          <Button size="sm" variant="ghost" type="button" onClick={copy} className="ml-auto">
            <CopyIcon className="size-3" />
            Copy
          </Button>
        </div>
        <pre className="bg-background text-2xs max-h-72 overflow-auto rounded border p-3 font-mono leading-snug">
          {SNIPPETS[lang]}
        </pre>
        <p className="text-muted-foreground text-xs">
          Rejects deliveries older than 5 minutes (replay protection) and uses constant-time
          comparison. Verify against the new secret after rotation; the previous secret stays valid
          for the rotation grace window.
        </p>
      </CollapsibleContent>
    </Collapsible>
  );
}
