"use client";

import { useMutation, useQuery } from "@apollo/client/react";
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
import { ListControls } from "@/components/ListControls";
import { useListControls } from "@/hooks/use-list-controls";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
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
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import type { MutationResult } from "@/graphql/identity/identity.types";
import {
  CREATE_WEBHOOK,
  DELETE_WEBHOOK,
  ROTATE_OUTBOUND_WEBHOOK_SECRET,
  TEST_FIRE_WEBHOOK,
  UPDATE_WEBHOOK,
} from "@/graphql/operations/operations.mutations";
import { LIST_WEBHOOK_DELIVERIES, LIST_WEBHOOKS } from "@/graphql/operations/operations.queries";
import { handleVersionMismatch } from "@/lib/apollo/version-mismatch";
import { DOC_LINKS } from "@/lib/docs/urls";
import { useFormatters } from "@/lib/i18n/formatters";
import type {
  AstroliftWebhookDelivery,
  AstroliftWebhookSecretReveal,
  AstroliftWebhookSubscription,
  AstroliftWebhookTestResult,
  WebhookFormat,
} from "@/graphql/operations/operations.types";

import { SNIPPETS, SNIPPET_LABELS, type SnippetLanguage } from "./verification-snippets";

interface Resp {
  astroliftWebhookSubscriptions: AstroliftWebhookSubscription[];
}

interface DeliveriesResp {
  astroliftWebhookDeliveries: AstroliftWebhookDelivery[];
}

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

export function WebhooksClient({
  appSlug,
  tabs,
}: { appSlug?: string; tabs?: React.ReactNode } = {}) {
  const fmt = useFormatters();
  const [open, setOpen] = React.useState(false);
  const [reveal, setReveal] = React.useState<AstroliftWebhookSecretReveal | null>(null);
  const [rotateTarget, setRotateTarget] = React.useState<AstroliftWebhookSubscription | null>(null);
  const [testTarget, setTestTarget] = React.useState<AstroliftWebhookSubscription | null>(null);
  const [deleteTarget, setDeleteTarget] = React.useState<AstroliftWebhookSubscription | null>(null);
  const [expandedId, setExpandedId] = React.useState<string | null>(null);
  const [testResult, setTestResult] = React.useState<AstroliftWebhookTestResult | null>(null);

  const [url, setUrl] = React.useState("");
  const [eventsRaw, setEventsRaw] = React.useState(SUGGESTED_EVENTS.join("\n"));
  const [format, setFormat] = React.useState<WebhookFormat>("generic");

  const variables = { appSlug: appSlug ?? null };
  const subs = useQuery<Resp>(LIST_WEBHOOKS, { variables });

  const refetchVars = [{ query: LIST_WEBHOOKS, variables }];

  const [createWebhook, { loading: creating }] = useMutation<{
    createWebhookSubscription: MutationResult<AstroliftWebhookSecretReveal>;
  }>(CREATE_WEBHOOK, {
    refetchQueries: refetchVars,
    awaitRefetchQueries: true,
  });

  const [updateWebhook] = useMutation<{
    updateWebhookSubscription: MutationResult<AstroliftWebhookSubscription>;
  }>(UPDATE_WEBHOOK, {
    refetchQueries: refetchVars,
    awaitRefetchQueries: true,
  });

  const [rotateSecret, { loading: rotating }] = useMutation<{
    rotateOutboundWebhookSecret: MutationResult<AstroliftWebhookSecretReveal>;
  }>(ROTATE_OUTBOUND_WEBHOOK_SECRET, {
    refetchQueries: refetchVars,
    awaitRefetchQueries: true,
  });

  const [testFireWebhook, { loading: firing }] = useMutation<{
    testWebhookSubscription: MutationResult<AstroliftWebhookTestResult>;
  }>(TEST_FIRE_WEBHOOK, {
    refetchQueries: refetchVars,
    awaitRefetchQueries: true,
  });

  const [deleteWebhook, { loading: deleting }] = useMutation<{
    deleteWebhookSubscription: MutationResult<{ id: string; deleted: boolean }>;
  }>(DELETE_WEBHOOK, {
    refetchQueries: refetchVars,
    awaitRefetchQueries: true,
  });

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const events = eventsRaw
      .split(/\s|,/g)
      .map((e) => e.trim())
      .filter(Boolean);
    const { data } = await createWebhook({
      variables: {
        input: { url: url.trim(), events, appSlug: appSlug ?? null, format },
      },
    });
    if (data?.createWebhookSubscription.ok && data.createWebhookSubscription.data) {
      setReveal(data.createWebhookSubscription.data);
      setOpen(false);
      setUrl("");
      setFormat("generic");
    } else {
      toast.error(data?.createWebhookSubscription.errors?.[0]?.message ?? "Create failed");
    }
  }

  async function handleDelete(s: AstroliftWebhookSubscription) {
    const { data } = await deleteWebhook({ variables: { input: { id: s.id } } });
    if (data?.deleteWebhookSubscription.ok) {
      toast.success("Deleted");
    } else {
      throw new Error(data?.deleteWebhookSubscription.errors?.[0]?.message ?? "Delete failed");
    }
  }

  async function handleToggleActive(s: AstroliftWebhookSubscription) {
    const next = !s.isActive;
    const { data } = await updateWebhook({
      variables: { input: { id: s.id, isActive: next, ifMatchVersion: s.version } },
    });
    if (data?.updateWebhookSubscription.ok) {
      toast.success(next ? "Resumed" : "Paused");
    } else if (
      handleVersionMismatch(data?.updateWebhookSubscription, {
        label: "webhook subscription",
        onRefresh: () => subs.refetch(),
      })
    ) {
      // toast already raised by helper
    } else {
      toast.error(data?.updateWebhookSubscription.errors?.[0]?.message ?? "Toggle failed");
    }
  }

  async function handleFormatChange(s: AstroliftWebhookSubscription, next: WebhookFormat) {
    const { data } = await updateWebhook({
      variables: { input: { id: s.id, format: next, ifMatchVersion: s.version } },
    });
    if (data?.updateWebhookSubscription.ok) {
      toast.success(`Format set to ${next}`);
    } else if (
      handleVersionMismatch(data?.updateWebhookSubscription, {
        label: "webhook subscription",
        onRefresh: () => subs.refetch(),
      })
    ) {
      // toast already raised by helper
    } else {
      toast.error(data?.updateWebhookSubscription.errors?.[0]?.message ?? "Update failed");
    }
  }

  async function handleRotate(s: AstroliftWebhookSubscription) {
    const { data } = await rotateSecret({ variables: { input: { id: s.id } } });
    if (data?.rotateOutboundWebhookSecret.ok && data.rotateOutboundWebhookSecret.data) {
      setReveal(data.rotateOutboundWebhookSecret.data);
      toast.success("Secret rotated — copy the new value now");
    } else {
      throw new Error(data?.rotateOutboundWebhookSecret.errors?.[0]?.message ?? "Rotation failed");
    }
  }

  async function handleTestFire(s: AstroliftWebhookSubscription) {
    const { data } = await testFireWebhook({ variables: { input: { id: s.id } } });
    if (data?.testWebhookSubscription.ok && data.testWebhookSubscription.data) {
      setTestResult(data.testWebhookSubscription.data);
      const code = data.testWebhookSubscription.data.statusCode;
      if (code && code >= 200 && code < 300) {
        toast.success(`Delivered: HTTP ${code}`);
      } else if (code) {
        toast.warning(`Subscriber returned HTTP ${code}`);
      } else {
        toast.error(`Transport failure: ${data.testWebhookSubscription.data.error || "unknown"}`);
      }
    } else {
      throw new Error(data?.testWebhookSubscription.errors?.[0]?.message ?? "Test fire failed");
    }
  }

  function copySecret() {
    if (!reveal) return;
    navigator.clipboard.writeText(reveal.plaintextSecret);
    toast.success("Copied");
  }

  const allSubs = subs.data?.astroliftWebhookSubscriptions ?? [];
  const subsCtrl = useListControls({
    data: allSubs,
    searchFn: (s) => [s.url ?? "", ...(s.events ?? [])].join(" "),
    initialPageSize: 25,
    sortFn: (a, b, sort) => {
      const dir = sort.dir === "asc" ? 1 : -1;
      if (sort.key === "url") return (a.url ?? "").localeCompare(b.url ?? "") * dir;
      return 0;
    },
  });
  const list = subsCtrl.rows;

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
              <Button size="sm" variant="ghost" onClick={() => setReveal(null)}>
                Dismiss
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {testResult && <TestResultCard result={testResult} onDismiss={() => setTestResult(null)} />}

      {allSubs.length > 0 && (
        <ListControls controls={subsCtrl} searchPlaceholder="Search webhooks…" className="mb-3" />
      )}
      <Card>
        <CardContent className="p-0">
          {subs.loading ? (
            <div className="space-y-2 p-6">
              <Skeleton className="h-12 w-full" />
            </div>
          ) : list.length === 0 ? (
            <div className="p-6">
              <EmptyState
                icon={<WebhookIcon className="size-5" />}
                title="No webhook subscriptions"
                description="Subscribe an external system (Zentinelle, Slack relay, custom collector) to platform events."
              />
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-8" />
                  <TableHead>URL</TableHead>
                  <TableHead>Events</TableHead>
                  <TableHead>Format</TableHead>
                  <TableHead>Last delivery</TableHead>
                  <TableHead>Failures</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {list.map((s) => (
                  <React.Fragment key={s.id}>
                    <TableRow
                      className={s.isActive ? "" : "text-muted-foreground line-through opacity-60"}
                    >
                      <TableCell className="w-8 p-2">
                        <Button
                          variant="ghost"
                          size="icon"
                          className="size-7"
                          onClick={() => setExpandedId((prev) => (prev === s.id ? null : s.id))}
                          aria-label={expandedId === s.id ? "Collapse" : "Expand"}
                        >
                          {expandedId === s.id ? (
                            <ChevronDownIcon className="size-4" />
                          ) : (
                            <ChevronRightIcon className="size-4" />
                          )}
                        </Button>
                      </TableCell>
                      <TableCell className="max-w-xs truncate font-mono text-xs">
                        {appSlug ? (
                          s.url
                        ) : (
                          <Link
                            href={`/webhooks/${s.id}`}
                            className="hover:text-[var(--brand-primary)] hover:underline"
                          >
                            {s.url}
                          </Link>
                        )}
                      </TableCell>
                      <TableCell>
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
                      </TableCell>
                      <TableCell>
                        <Can
                          permission="webhook.update"
                          fallback={
                            <Badge variant="outline" className="text-xs capitalize">
                              {s.format}
                            </Badge>
                          }
                        >
                          <Select
                            value={s.format}
                            onValueChange={(v) => handleFormatChange(s, v as WebhookFormat)}
                          >
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
                      </TableCell>
                      <TableCell className="text-muted-foreground text-sm">
                        {s.lastDeliveryAt
                          ? `${fmt.formatDateTime(s.lastDeliveryAt)} (${s.lastResponseStatus ?? "?"})`
                          : "never"}
                      </TableCell>
                      <TableCell className="font-mono text-xs">
                        {s.failureCount > 0 ? (
                          <span className="text-destructive inline-flex items-center gap-1">
                            <AlertTriangleIcon className="size-3" />
                            {s.failureCount}
                          </span>
                        ) : (
                          "0"
                        )}
                      </TableCell>
                      <TableCell>
                        <Badge variant={s.isActive ? "secondary" : "outline"}>
                          {s.isActive ? "active" : "paused"}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-right">
                        <div className="flex justify-end gap-1">
                          <Can permission="webhook.update">
                            <Button
                              size="sm"
                              variant="ghost"
                              title={s.isActive ? "Pause" : "Resume"}
                              onClick={() => handleToggleActive(s)}
                            >
                              {s.isActive ? (
                                <PauseIcon className="size-4" />
                              ) : (
                                <PlayIcon className="size-4" />
                              )}
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
                      </TableCell>
                    </TableRow>
                    {expandedId === s.id && (
                      <TableRow>
                        <TableCell colSpan={8} className="bg-muted/30 p-0">
                          <ExpandedRow subscription={s} />
                        </TableCell>
                      </TableRow>
                    )}
                  </React.Fragment>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

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
          if (deleteTarget) await handleDelete(deleteTarget);
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
          if (rotateTarget) await handleRotate(rotateTarget);
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
          if (testTarget) await handleTestFire(testTarget);
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

function ExpandedRow({ subscription }: { subscription: AstroliftWebhookSubscription }) {
  const fmt = useFormatters();
  const { data, loading } = useQuery<DeliveriesResp>(LIST_WEBHOOK_DELIVERIES, {
    variables: { subscriptionId: subscription.id, limit: 10 },
    fetchPolicy: "cache-and-network",
  });
  const allDeliveries = data?.astroliftWebhookDeliveries ?? [];
  const deliveriesCtrl = useListControls({
    data: allDeliveries,
    searchFn: (d) => [d.eventType, d.success ? "success" : "failed"].join(" "),
    initialPageSize: 25,
  });
  const rows = deliveriesCtrl.rows;

  return (
    <div className="space-y-3 p-4">
      <DefinitionList
        orientation="stack"
        className="grid grid-cols-2 gap-3 md:grid-cols-4"
        items={[
          {
            term: "Secret rotated",
            description: subscription.secretRotatedAt
              ? fmt.formatDateTime(subscription.secretRotatedAt)
              : "never",
          },
          {
            term: "Last delivery",
            description: subscription.lastDeliveryAt
              ? fmt.formatDateTime(subscription.lastDeliveryAt)
              : "never",
          },
          { term: "Last status", description: subscription.lastResponseStatus ?? "—" },
          { term: "Failure count", description: subscription.failureCount },
        ]}
      />

      <div>
        <div className="mb-1 flex items-center justify-between">
          <p className="text-muted-foreground text-xs font-medium tracking-wide uppercase">
            Recent deliveries
          </p>
          {allDeliveries.length > 0 && (
            <ListControls controls={deliveriesCtrl} hideSearch className="!mb-0" />
          )}
        </div>
        {loading && rows.length === 0 ? (
          <Skeleton className="h-16 w-full" />
        ) : rows.length === 0 ? (
          <p className="text-muted-foreground text-xs">
            No deliveries yet. Use the Send test event button to fire one.
          </p>
        ) : (
          <div className="bg-background overflow-hidden rounded">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-12">#</TableHead>
                  <TableHead>When</TableHead>
                  <TableHead>Event</TableHead>
                  <TableHead className="w-16">Status</TableHead>
                  <TableHead className="w-20">Latency</TableHead>
                  <TableHead className="w-16">Test?</TableHead>
                  <TableHead>Response</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((d) => (
                  <TableRow key={d.id}>
                    <TableCell className="font-mono text-xs">{d.retryAttempt}</TableCell>
                    <TableCell className="text-xs">{fmt.formatDateTime(d.deliveredAt)}</TableCell>
                    <TableCell className="font-mono text-xs">{d.eventType}</TableCell>
                    <TableCell>
                      <Badge variant={d.success ? "secondary" : "outline"} className="text-2xs">
                        {d.statusCode ?? "ERR"}
                      </Badge>
                    </TableCell>
                    <TableCell className="font-mono text-xs">{d.latencyMs}ms</TableCell>
                    <TableCell>
                      {d.isTest && (
                        <Badge variant="outline" className="text-2xs">
                          test
                        </Badge>
                      )}
                    </TableCell>
                    <TableCell className="max-w-xs truncate font-mono text-xs">
                      {d.error || d.responseBodyExcerpt || "—"}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </div>
    </div>
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
