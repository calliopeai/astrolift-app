"use client";

import {
  AlertTriangleIcon,
  BookOpenIcon,
  CheckCircle2Icon,
  ChevronRightIcon,
  CopyIcon,
  HistoryIcon,
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
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import type { Column } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import { adminCrumbs } from "@/components/screens/administration/insights/header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { DefinitionList } from "@/components/ui/definition-list";
import { DropdownMenuItem, DropdownMenuSeparator } from "@/components/ui/dropdown-menu";
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

const FORMAT_OPTIONS: { value: WebhookFormat }[] = [
  { value: "generic" },
  { value: "slack" },
  { value: "discord" },
];

export type WebhooksScreenProps = WebhooksData & {
  /** Set on the app/agent-scoped tab; unset on the platform-wide surface. */
  appSlug?: string;
  tabs?: React.ReactNode;
  /** The deliveries sheet's body. The route fills it with a container so
   *  its delivery feed runs only while the sheet is open. */
  renderDetail: (subscription: AstroliftWebhookSubscription) => React.ReactNode;
};

/**
 * Admin › Webhooks (spec 44 §5.1), or an app's Settings › Webhooks section
 * (embedded, `appSlug`): the subscriptions on the shared list, views All ·
 * Mine · Failing · Paused, cursor paged, row actions in `⋯`. A
 * subscription's delivery log opens in a sheet (platform-wide, the row also
 * links to its detail page), so the page holds one list. Pure; the data half
 * is useWebhooks.
 */
export function WebhooksScreen({
  appSlug,
  tabs,
  renderDetail,
  list,
  rows,
  totalCount,
  nextCursor,
  loading,
  error,
  onRetry,
  creating,
  pendingRows,
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
  const t = useTranslations("webhooksScreen");
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

  // The sheet's subscription is resolved against the page in hand, so
  // paging away (or searching past it) closes the sheet rather than
  // leaving it describing a row that is no longer on screen.
  const expanded = rows.find((s) => s.id === expandedId) ?? null;

  const columns: Column<AstroliftWebhookSubscription>[] = [
    {
      id: "url",
      header: t("columns.url"),
      cellClassName: "max-w-xs",
      cell: (s) => (
        <span className="block truncate font-mono text-xs" title={s.url}>
          {s.url}
        </span>
      ),
    },
    {
      id: "events",
      header: t("columns.events"),
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
      header: t("columns.format"),
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
            {/* Above the row's stretched link, so the select opens instead of the row. */}
            <SelectTrigger size="sm" className="relative z-10 h-7 w-32 text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {FORMAT_OPTIONS.map((o) => (
                <SelectItem key={o.value} value={o.value}>
                  {t(`formats.${o.value}.label`)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Can>
      ),
    },
    {
      id: "lastDelivery",
      header: t("columns.lastDelivery"),
      cellClassName: "text-muted-foreground text-sm",
      cell: (s) =>
        s.lastDeliveryAt
          ? `${fmt.formatDateTime(s.lastDeliveryAt)} (${s.lastResponseStatus ?? "?"})`
          : t("never"),
    },
    {
      id: "failures",
      header: t("columns.failures"),
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
      header: t("columns.status"),
      cell: (s) => (
        <Badge variant={s.isActive ? "secondary" : "outline"}>
          {s.isActive ? t("active") : t("paused")}
        </Badge>
      ),
    },
  ];

  function rowActions(s: AstroliftWebhookSubscription) {
    const busy = pendingRows.has(s.id);
    return (
      <>
        <DropdownMenuItem onSelect={() => setExpandedId(s.id)}>
          <HistoryIcon className="size-4" />
          {t("deliveries")}
        </DropdownMenuItem>
        <Can permission="webhook.update">
          <DropdownMenuItem disabled={busy} onSelect={() => void onToggleActive(s)}>
            {s.isActive ? <PauseIcon className="size-4" /> : <PlayIcon className="size-4" />}
            {s.isActive ? t("pause") : t("resume")}
          </DropdownMenuItem>
          <DropdownMenuItem disabled={busy || !s.isActive} onSelect={() => setTestTarget(s)}>
            <SendIcon className="size-4" />
            {t("sendTestEvent")}
          </DropdownMenuItem>
          <DropdownMenuItem disabled={busy} onSelect={() => setRotateTarget(s)}>
            <KeyRoundIcon className="size-4" />
            {t("rotateSecret")}
          </DropdownMenuItem>
        </Can>
        <Can permission="webhook.delete">
          <DropdownMenuSeparator />
          <DropdownMenuItem
            variant="destructive"
            disabled={busy}
            onSelect={() => setDeleteTarget(s)}
          >
            <Trash2Icon className="size-4" />
            {t("delete")}
          </DropdownMenuItem>
        </Can>
      </>
    );
  }

  const description = appSlug
    ? t.rich("appDescription", {
        app: appSlug,
        mono: (chunks) => <span className="font-mono">{chunks}</span>,
      })
    : t("platformDescription");

  const actions = (
    <>
      <Button asChild size="sm" variant="outline">
        <Link href={DOC_LINKS.webhooks}>
          <BookOpenIcon className="size-4" />
          {t("learnMore")}
        </Link>
      </Button>
      <Can permission="webhook.create">
        <Button onClick={() => setOpen(true)}>
          <PlusIcon className="size-4" />
          {t("newWebhook")}
        </Button>
      </Can>
    </>
  );

  const notice =
    reveal || testResult ? (
      <>
        {reveal && (
          <Card className="border-success-border bg-success/5">
            <CardContent className="flex flex-col gap-3 p-4">
              <div className="flex min-w-0 items-center gap-2">
                <CheckCircle2Icon className="text-success-fg size-4 shrink-0" />
                <p className="min-w-0 text-sm font-medium [overflow-wrap:anywhere]">
                  {t.rich("secretReady", {
                    url: reveal.subscription.url,
                    mono: (chunks) => <span className="font-mono">{chunks}</span>,
                  })}
                </p>
              </div>
              <p className="text-muted-foreground text-xs">{t("secretRevealHelp")}</p>
              <div className="flex min-w-0 items-center gap-2">
                <code className="bg-background min-w-0 flex-1 rounded-md border px-3 py-2 font-mono text-xs break-all">
                  {reveal.plaintextSecret}
                </code>
                <Button size="sm" variant="outline" onClick={copySecret}>
                  <CopyIcon className="size-4" />
                  {t("copy")}
                </Button>
              </div>
              <div className="flex justify-end">
                <Button size="sm" variant="ghost" onClick={dismissReveal}>
                  {t("dismiss")}
                </Button>
              </div>
            </CardContent>
          </Card>
        )}
        {testResult && <TestResultCard result={testResult} onDismiss={dismissTestResult} />}
      </>
    ) : undefined;

  const body = {
    list,
    label: t("listLabel"),
    columns,
    rows,
    getRowId: (s: AstroliftWebhookSubscription) => s.id,
    rowActions,
    rowClassName: (s: AstroliftWebhookSubscription) =>
      s.isActive ? undefined : "text-muted-foreground line-through opacity-60",
    loading,
    error,
    onRetry,
    totalCount,
    nextCursor,
    notice,
    empty: {
      icon: <WebhookIcon className="size-5" />,
      title: t("empty.title"),
      description: t("empty.description"),
    },
  };

  return (
    <>
      {appSlug ? (
        <div className="flex min-w-0 flex-1 flex-col gap-4">
          {tabs}
          <div className="flex min-w-0 flex-wrap items-start justify-between gap-3">
            <p className="text-muted-foreground max-w-3xl min-w-0 text-sm">{description}</p>
            <div className="flex shrink-0 items-center gap-2">{actions}</div>
          </div>
          <ListPage<AstroliftWebhookSubscription> embedded {...body} />
        </div>
      ) : (
        <ListPage<AstroliftWebhookSubscription>
          header={{
            crumbs: adminCrumbs("webhooks", t("title")),
            title: t("title"),
            context: description,
            primaryAction: <div className="flex items-center gap-2">{actions}</div>,
          }}
          rowHref={(s) => `/webhooks/${s.id}`}
          {...body}
        />
      )}

      <Sheet
        open={expanded !== null}
        onOpenChange={(next) => {
          if (!next) setExpandedId(null);
        }}
      >
        <SheetContent className="flex w-full flex-col sm:max-w-xl">
          <SheetHeader>
            <SheetTitle>{t("deliveries")}</SheetTitle>
            <SheetDescription>{t("deliveriesDescription")}</SheetDescription>
          </SheetHeader>
          <div className="min-w-0 flex-1 overflow-y-auto px-4 pb-4">
            {expanded && (
              // Keyed by id so opening a different subscription starts its
              // own delivery feed rather than inheriting the previous one's.
              <React.Fragment key={expanded.id}>{renderDetail(expanded)}</React.Fragment>
            )}
          </div>
        </SheetContent>
      </Sheet>

      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent className="flex flex-col">
          <SheetHeader>
            <SheetTitle>{t("newWebhook")}</SheetTitle>
            <SheetDescription>{t("createDescription")}</SheetDescription>
          </SheetHeader>
          <form onSubmit={submit} className="flex flex-1 flex-col gap-4 px-4 pb-4">
            <div className="space-y-2">
              <Label htmlFor="webhook-url">{t("columns.url")}</Label>
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
              <Label htmlFor="webhook-format">{t("columns.format")}</Label>
              <Select value={format} onValueChange={(v) => setFormat(v as WebhookFormat)}>
                <SelectTrigger id="webhook-format">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {FORMAT_OPTIONS.map((o) => (
                    <SelectItem key={o.value} value={o.value}>
                      {t(`formats.${o.value}.label`)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-muted-foreground text-xs">{t(`formats.${format}.hint`)}</p>
            </div>
            <div className="space-y-2">
              <Label htmlFor="events">{t("columns.events")}</Label>
              <Textarea
                id="events"
                value={eventsRaw}
                onChange={(e) => setEventsRaw(e.target.value)}
                rows={8}
                className="font-mono text-xs"
              />
              <p className="text-muted-foreground text-xs">
                {t.rich("eventsHelp", { code: (chunks) => <code>{chunks}</code> })}
              </p>
            </div>
            <VerificationSnippetDisclosure />
            <SheetFooter className="mt-auto flex-row justify-end gap-2 px-0">
              <Button type="button" variant="outline" onClick={() => setOpen(false)}>
                {t("cancel")}
              </Button>
              <Button type="submit" disabled={creating || !url}>
                {creating ? t("creating") : t("createWebhook")}
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
        title={t("deleteDialog.title")}
        description={
          deleteTarget ? t("deleteDialog.description", { url: deleteTarget.url }) : undefined
        }
        confirmLabel={t("deleteDialog.confirm")}
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
        title={t("rotateDialog.title")}
        description={
          rotateTarget ? t("rotateDialog.description", { url: rotateTarget.url }) : undefined
        }
        confirmLabel={t("rotateSecret")}
        onConfirm={async () => {
          if (rotateTarget) await onRotate(rotateTarget);
        }}
      />

      <ConfirmDialog
        open={testTarget !== null}
        onOpenChange={(next) => {
          if (!next) setTestTarget(null);
        }}
        title={t("testDialog.title")}
        description={testTarget ? t("testDialog.description", { url: testTarget.url }) : undefined}
        confirmLabel={t("testDialog.confirm")}
        onConfirm={async () => {
          if (testTarget) await onTestFire(testTarget);
        }}
      />
    </>
  );
}

function TestResultCard({
  result,
  onDismiss,
}: {
  result: AstroliftWebhookTestResult;
  onDismiss: () => void;
}) {
  const t = useTranslations("webhooksScreen");
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
            {t.rich("testDeliveryTo", {
              url: result.url,
              mono: (chunks) => <span className="font-mono">{chunks}</span>,
            })}
          </div>
          <Button size="sm" variant="ghost" onClick={onDismiss}>
            {t("dismiss")}
          </Button>
        </div>
        <DefinitionList
          orientation="stack"
          className="grid grid-cols-3 gap-3 text-xs"
          items={[
            { term: t("columns.status"), description: result.statusCode ?? "—" },
            { term: t("latency"), description: t("ms", { ms: result.durationMs }) },
            {
              term: t("deliveryId"),
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
  const t = useTranslations("webhooksScreen");
  const [lang, setLang] = React.useState<SnippetLanguage>("node");
  const langs: SnippetLanguage[] = ["node", "python", "go"];

  function copy() {
    navigator.clipboard.writeText(SNIPPETS[lang]);
    toast.success(t("snippetCopied"));
  }

  return (
    <Collapsible>
      <CollapsibleTrigger asChild>
        <Button variant="outline" size="sm" type="button" className="w-full justify-start">
          <ChevronRightIcon className="size-4" />
          {t("showSnippet")}
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
            {t("copy")}
          </Button>
        </div>
        <pre className="bg-background text-2xs max-h-72 overflow-auto rounded border p-3 font-mono leading-snug">
          {SNIPPETS[lang]}
        </pre>
        <p className="text-muted-foreground text-xs">{t("snippetHelp")}</p>
      </CollapsibleContent>
    </Collapsible>
  );
}
