"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import {
  AlertTriangleIcon,
  BookOpenIcon,
  CheckCircle2Icon,
  CopyIcon,
  PlusIcon,
  Trash2Icon,
  WebhookIcon,
} from "lucide-react";
import Link from "next/link";
import * as React from "react";
import { toast } from "sonner";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
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
import { CREATE_WEBHOOK, DELETE_WEBHOOK } from "@/graphql/operations/operations.mutations";
import { LIST_WEBHOOKS } from "@/graphql/operations/operations.queries";
import type {
  AstroliftWebhookSecretReveal,
  AstroliftWebhookSubscription,
} from "@/graphql/operations/operations.types";

interface Resp {
  astroliftWebhookSubscriptions: AstroliftWebhookSubscription[];
}

const SUGGESTED_EVENTS = [
  "APP_REGISTERED",
  "DEPLOY_STARTED",
  "DEPLOY_SUCCEEDED",
  "DEPLOY_FAILED",
  "PREVIEW_CREATED",
  "PREVIEW_TORN_DOWN",
];

export function WebhooksClient({ appSlug }: { appSlug?: string } = {}) {
  const [open, setOpen] = React.useState(false);
  const [reveal, setReveal] = React.useState<AstroliftWebhookSecretReveal | null>(null);
  const [deleteTarget, setDeleteTarget] = React.useState<AstroliftWebhookSubscription | null>(null);
  const [url, setUrl] = React.useState("");
  const [eventsRaw, setEventsRaw] = React.useState(SUGGESTED_EVENTS.join("\n"));

  const variables = { appSlug: appSlug ?? null };
  const subs = useQuery<Resp>(LIST_WEBHOOKS, { variables });

  const refetchVars = [{ query: LIST_WEBHOOKS, variables }];

  const [createWebhook, { loading: creating }] = useMutation<{
    createWebhookSubscription: MutationResult<AstroliftWebhookSecretReveal>;
  }>(CREATE_WEBHOOK, {
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
        input: { url: url.trim(), events, appSlug: appSlug ?? null },
      },
    });
    if (data?.createWebhookSubscription.ok && data.createWebhookSubscription.data) {
      setReveal(data.createWebhookSubscription.data);
      setOpen(false);
      setUrl("");
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

  function copySecret() {
    if (!reveal) return;
    navigator.clipboard.writeText(reveal.plaintextSecret);
    toast.success("Copied");
  }

  const list = subs.data?.astroliftWebhookSubscriptions ?? [];

  return (
    <PageShell
      title="Webhooks"
      description={
        appSlug
          ? `Outbound HTTP delivery for ${appSlug}'s event stream. Each subscription's secret is used to HMAC-sign every payload.`
          : "Outbound HTTP delivery for the platform's event log. Each subscription's secret is used to HMAC-sign every payload."
      }
      actions={
        <>
          <Button asChild size="sm" variant="outline">
            <Link href="/documentation/webhooks">
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
      {reveal && (
        <Card className="border-emerald-500/30 bg-emerald-500/5">
          <CardContent className="flex flex-col gap-3 p-4">
            <div className="flex items-center gap-2">
              <CheckCircle2Icon className="size-4 text-emerald-600" />
              <p className="text-sm font-medium">
                Webhook to <span className="font-mono">{reveal.subscription.url}</span> created
              </p>
            </div>
            <p className="text-muted-foreground text-xs">
              Save the HMAC secret below — it&apos;s never shown again. We store only its SHA-256.
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
                  <TableHead>URL</TableHead>
                  <TableHead>Events</TableHead>
                  <TableHead>Last delivery</TableHead>
                  <TableHead>Failures</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {list.map((s) => (
                  <TableRow key={s.id}>
                    <TableCell className="max-w-xs truncate font-mono text-xs">{s.url}</TableCell>
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
                    <TableCell className="text-muted-foreground text-sm">
                      {s.lastDeliveryAt
                        ? `${new Date(s.lastDeliveryAt).toLocaleString()} (${s.lastResponseStatus ?? "?"})`
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
                    </TableCell>
                  </TableRow>
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
    </PageShell>
  );
}
