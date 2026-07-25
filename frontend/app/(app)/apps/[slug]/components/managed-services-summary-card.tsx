"use client";

import { useLazyQuery, useMutation, useQuery } from "@apollo/client/react";
import {
  AlertTriangleIcon,
  ArchiveIcon,
  CopyIcon,
  DatabaseIcon,
  EyeIcon,
  ExternalLinkIcon,
  GaugeIcon,
  HardDriveIcon,
  Loader2Icon,
  MailIcon,
  MoreHorizontalIcon,
  PlugIcon,
  RefreshCwIcon,
  SendIcon,
  ServerIcon,
} from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import { Can } from "@/components/Can";
import { StatusDot } from "@/components/StatusDot";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Section } from "@/components/ui/section";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { MutationResult } from "@/graphql/identity/identity.types";
import {
  REVEAL_MANAGED_SERVICE_CONNECTION,
  SEND_MANAGED_SERVICE_TEST_EMAIL,
} from "@/graphql/services/services.mutations";
import {
  GET_MANAGED_SERVICE_QUEUE_DEPTH,
  LIST_MANAGED_SERVICES,
  LIST_MANAGED_SERVICE_OBJECTS,
} from "@/graphql/services/services.queries";
import type {
  AstroliftManagedService,
  AstroliftManagedServiceConnection,
  AstroliftManagedServiceObjects,
  AstroliftManagedServiceQueueDepth,
  AstroliftManagedServiceTestEmailResult,
} from "@/graphql/services/services.types";
import { useFormatters } from "@/lib/i18n/formatters";

import { appPath, useAppChrome } from "./app-chrome-context";

interface Resp {
  astroliftManagedServices: AstroliftManagedService[];
}

interface RevealResp {
  revealManagedServiceConnection: MutationResult<AstroliftManagedServiceConnection>;
}

interface SendTestEmailResp {
  sendManagedServiceTestEmail: MutationResult<AstroliftManagedServiceTestEmailResult>;
}

interface ObjectsResp {
  astroliftManagedServiceObjects: AstroliftManagedServiceObjects | null;
}

interface QueueDepthResp {
  astroliftManagedServiceQueueDepth: AstroliftManagedServiceQueueDepth | null;
}

/** Compact human-readable size string. Avoids pulling in a new dep
 *  for a single-shot display in a side dialog. */
function formatBytes(n: number): string {
  if (!Number.isFinite(n) || n < 0) return "—";
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v < 10 ? v.toFixed(1) : Math.round(v)} ${units[i]}`;
}

/** "12s" / "3m" / "2h" / "1d" — uses absolute seconds so the snapshot
 *  age renders without pulling in a duration formatter dep. */
function formatSecondsShort(s: number): string {
  if (!Number.isFinite(s) || s < 0) return "—";
  if (s < 60) return `${Math.round(s)}s`;
  if (s < 3600) return `${Math.round(s / 60)}m`;
  if (s < 86400) return `${Math.round(s / 3600)}h`;
  return `${Math.round(s / 86400)}d`;
}

/**
 * Maps a managed-service status string to the StatusDot palette.
 * Mirrors the table in `managed-services-client.tsx` so the summary
 * card and the full table render the same dot for the same row.
 */
const STATUS_DOT: Record<string, "ok" | "warn" | "error" | "pending" | "muted"> = {
  active: "ok",
  pending: "warn",
  provisioning: "pending",
  updating: "pending",
  deprovisioning: "pending",
  failed: "error",
  deleted: "muted",
};

/** Stable sort priority — failed first (needs attention), then in-flight,
 *  then active, then deprovisioning, then deleted/unknown. */
const STATUS_ORDER: Record<string, number> = {
  failed: 0,
  pending: 1,
  provisioning: 1,
  updating: 1,
  active: 2,
  deprovisioning: 3,
  deleted: 4,
};

const KIND_ICON: Record<string, React.ElementType> = {
  postgres: DatabaseIcon,
  mysql: DatabaseIcon,
  redis: DatabaseIcon,
  document_db: DatabaseIcon,
  object_store: HardDriveIcon,
  queue: ArchiveIcon,
  topic: ArchiveIcon,
  email: MailIcon,
  mq: ArchiveIcon,
  search: ServerIcon,
  kv_store: DatabaseIcon,
  vector_index: ServerIcon,
  time_series: ServerIcon,
  nfs: HardDriveIcon,
  model_endpoint: ServerIcon,
};

/** Kinds that get the "reveal connection" action — anything with an
 *  envelope worth showing (postgres, redis, mysql, etc.). */
const CONNECTION_REVEAL_KINDS = new Set([
  "postgres",
  "mysql",
  "redis",
  "kv_store",
  "search",
  "document_db",
  "vector_index",
  "time_series",
]);

const OBJECT_STORE_KINDS = new Set(["object_store"]);
const QUEUE_KINDS = new Set(["queue", "topic"]);
const EMAIL_KINDS = new Set(["email"]);

/** Maps a backend `last_action_kind` to a human label.  Falls back to
 *  the raw key when the i18n bundle doesn't carry it yet (forward-
 *  compatible for new action kinds without breaking existing locales). */
function lastActionLabel(t: ReturnType<typeof useTranslations>, kind: string): string {
  const known = new Set(["connection.reveal", "test_email.send"]);
  if (known.has(kind)) {
    return t(`actionLabels.${kind}`);
  }
  return kind;
}

/**
 * Inline managed-services summary card for the app Settings landing
 * (#401).
 *
 * One row per bound service with kind icon + name + variant + status
 * pill + env badge.  Per-row dropdown surfaces kind-specific quick
 * actions (reveal connection, send test email, list objects, view
 * depth) without forcing operators to navigate to the full
 * `/managed-services` page.
 *
 * The card hides entirely when the app has zero bound services so the
 * Settings landing doesn't carry empty real estate for the (common)
 * single-workload apps that don't ship any.
 */
export function ManagedServicesSummaryCard({ appSlug }: { appSlug: string }) {
  const chrome = useAppChrome();
  const t = useTranslations("apps.settings.managedServicesSummary");
  const { data, loading } = useQuery<Resp>(LIST_MANAGED_SERVICES, {
    variables: { appSlug, environmentName: null },
    fetchPolicy: "cache-and-network",
  });
  const services = React.useMemo(() => {
    const list = data?.astroliftManagedServices ?? [];
    return [...list].sort((a, b) => {
      const orderA = STATUS_ORDER[a.status] ?? 5;
      const orderB = STATUS_ORDER[b.status] ?? 5;
      if (orderA !== orderB) return orderA - orderB;
      return a.name.localeCompare(b.name);
    });
  }, [data]);

  // First-load skeleton hides the section to avoid flashing an empty
  // card then a populated one; subsequent refetches re-render in place.
  if (loading && !data) {
    return (
      <Section
        title={
          <span className="flex items-center gap-2">
            <PlugIcon className="text-primary size-4" />
            {t("title")}
          </span>
        }
      >
        <div>
          <Skeleton className="h-12 w-full" />
          <Skeleton className="mt-2 h-12 w-full" />
        </div>
      </Section>
    );
  }

  // Empty state: hide entirely per acceptance criteria. Operators reach
  // the full provisioning surface from the LINK_SECTIONS card below.
  if (services.length === 0) {
    return null;
  }

  return (
    <Section
      title={
        <span className="flex items-center gap-2">
          <PlugIcon className="text-primary size-4" />
          {t("title")}
          <Badge variant="secondary" className="text-2xs ml-1">
            {services.length}
          </Badge>
        </span>
      }
      description={t("description")}
      action={
        <Button asChild variant="outline" size="sm">
          <Link href={appPath(chrome, appSlug, "managed-services")}>{t("openAll")}</Link>
        </Button>
      }
    >
      <Card className="overflow-hidden p-0">
        <ul className="divide-y">
          {services.map((svc) => (
            <ManagedServiceSummaryRow key={svc.id} svc={svc} appSlug={appSlug} />
          ))}
        </ul>
      </Card>
    </Section>
  );
}

function ManagedServiceSummaryRow({
  svc,
  appSlug,
}: {
  svc: AstroliftManagedService;
  appSlug: string;
}) {
  const chrome = useAppChrome();
  const t = useTranslations("apps.settings.managedServicesSummary");
  const fmt = useFormatters();
  const KindIcon = KIND_ICON[svc.kind] ?? PlugIcon;
  const dotStatus = STATUS_DOT[svc.status] ?? "muted";

  const [revealOpen, setRevealOpen] = React.useState(false);
  const [emailOpen, setEmailOpen] = React.useState(false);
  const [objectsOpen, setObjectsOpen] = React.useState(false);
  const [depthOpen, setDepthOpen] = React.useState(false);

  const isConnectionKind = CONNECTION_REVEAL_KINDS.has(svc.kind);
  const isObjectStore = OBJECT_STORE_KINDS.has(svc.kind);
  const isQueue = QUEUE_KINDS.has(svc.kind);
  const isEmail = EMAIL_KINDS.has(svc.kind);
  const hasAction = isConnectionKind || isObjectStore || isQueue || isEmail;

  return (
    <li className="flex items-center gap-3 px-4 py-3">
      <Tooltip>
        <TooltipTrigger asChild>
          <div className="flex items-center gap-1.5">
            <StatusDot status={dotStatus} />
            <KindIcon className="text-muted-foreground size-4" />
          </div>
        </TooltipTrigger>
        <TooltipContent>
          <p className="text-xs">{t("statusTooltip", { kind: svc.kind, status: svc.status })}</p>
          {svc.statusError ? (
            <p className="text-destructive text-2xs mt-1 max-w-xs">{svc.statusError}</p>
          ) : null}
        </TooltipContent>
      </Tooltip>

      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-baseline gap-1.5">
          <span className="text-foreground font-mono text-sm">{svc.name || svc.kind}</span>
          <Badge variant="secondary" className="text-2xs">
            {svc.kind}
            {svc.variant ? `/${svc.variant}` : ""}
          </Badge>
          <Badge variant="outline" className="text-2xs font-mono">
            {svc.environmentName}
          </Badge>
          {svc.status === "failed" ? (
            <Badge
              variant="outline"
              className="border-destructive/40 bg-destructive/10 text-destructive text-2xs flex items-center gap-1"
            >
              <AlertTriangleIcon className="size-3" />
              {t("statusFailed")}
            </Badge>
          ) : null}
        </div>
        {svc.lastActionAt && svc.lastActionKind ? (
          <p className="text-muted-foreground text-2xs mt-0.5">
            {t("lastAction", {
              action: lastActionLabel(t, svc.lastActionKind),
              when: fmt.formatRelativeTime(svc.lastActionAt),
            })}
          </p>
        ) : null}
      </div>

      <div className="flex shrink-0 items-center gap-1">
        <Button asChild variant="ghost" size="sm" className="text-xs">
          <Link href={appPath(chrome, appSlug, "managed-services")}>
            {t("viewDetail")}
            <ExternalLinkIcon className="size-3" />
          </Link>
        </Button>
        {hasAction ? (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon" className="size-8">
                <MoreHorizontalIcon className="size-4" />
                <span className="sr-only">{t("actionsLabel")}</span>
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-56">
              <DropdownMenuLabel className="text-xs">{t("actionsLabel")}</DropdownMenuLabel>
              <DropdownMenuSeparator />
              {isConnectionKind ? (
                <Can permission="managed_service.update">
                  <DropdownMenuItem onSelect={() => setRevealOpen(true)}>
                    <EyeIcon className="size-4" />
                    {t("actions.connection.reveal")}
                  </DropdownMenuItem>
                </Can>
              ) : null}
              {isObjectStore ? (
                <DropdownMenuItem onSelect={() => setObjectsOpen(true)}>
                  <HardDriveIcon className="size-4" />
                  {t("actions.objects.list")}
                </DropdownMenuItem>
              ) : null}
              {isEmail ? (
                <Can permission="managed_service.update">
                  <DropdownMenuItem onSelect={() => setEmailOpen(true)}>
                    <SendIcon className="size-4" />
                    {t("actions.email.send")}
                  </DropdownMenuItem>
                </Can>
              ) : null}
              {isQueue ? (
                <DropdownMenuItem onSelect={() => setDepthOpen(true)}>
                  <GaugeIcon className="size-4" />
                  {t("actions.queue.depth")}
                </DropdownMenuItem>
              ) : null}
            </DropdownMenuContent>
          </DropdownMenu>
        ) : null}
      </div>

      {isConnectionKind ? (
        <RevealConnectionDialog svc={svc} open={revealOpen} onOpenChange={setRevealOpen} />
      ) : null}
      {isEmail ? (
        <SendTestEmailDialog svc={svc} open={emailOpen} onOpenChange={setEmailOpen} />
      ) : null}
      {isObjectStore ? (
        <ListObjectsDialog svc={svc} open={objectsOpen} onOpenChange={setObjectsOpen} />
      ) : null}
      {isQueue ? <QueueDepthDialog svc={svc} open={depthOpen} onOpenChange={setDepthOpen} /> : null}
    </li>
  );
}

// ─── reveal connection dialog ───────────────────────────────────────

/**
 * Reveal-connection modal — mirrors the #424 reveal pattern.
 *
 * Calls the audit-logged `revealManagedServiceConnection` mutation;
 * displays the envelope's key set with copy-to-clipboard per key.
 * Values are NEVER plaintext (they live in the secrets backend); the
 * dialog renders `secret-ref:<ref>` / `placeholder:pending` shims so
 * operators can see the wiring without us leaking credentials.
 */
function RevealConnectionDialog({
  svc,
  open,
  onOpenChange,
}: {
  svc: AstroliftManagedService;
  open: boolean;
  onOpenChange: (next: boolean) => void;
}) {
  const t = useTranslations("apps.settings.managedServicesSummary.revealDialog");
  const fmt = useFormatters();
  const [revealed, setRevealed] = React.useState<AstroliftManagedServiceConnection | null>(null);
  const [reveal, { loading }] = useMutation<RevealResp>(REVEAL_MANAGED_SERVICE_CONNECTION, {
    refetchQueries: [
      {
        query: LIST_MANAGED_SERVICES,
        variables: { appSlug: svc.registeredAppSlug, environmentName: null },
      },
    ],
  });

  React.useEffect(() => {
    if (!open) {
      setRevealed(null);
      return;
    }
    void (async () => {
      try {
        const { data } = await reveal({
          variables: { input: { managedServiceId: svc.id } },
        });
        const env = data?.revealManagedServiceConnection;
        if (!env) {
          toast.error(t("noResponse"));
          return;
        }
        if (!env.ok) {
          toast.error(env.errors[0]?.message ?? t("failed"));
          return;
        }
        setRevealed(env.data ?? null);
      } catch (err) {
        toast.error(err instanceof Error ? err.message : t("failed"));
      }
    })();
  }, [open, reveal, svc.id, t]);

  function handleCopy(value: string) {
    void navigator.clipboard.writeText(value).then(
      () => toast.success(t("copied")),
      () => toast.error(t("copyFailed"))
    );
  }

  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent className="max-h-[85vh] overflow-y-auto">
        <AlertDialogHeader>
          <AlertDialogTitle className="flex items-center gap-2">
            <EyeIcon className="size-4" />
            {t("title", { name: svc.name || svc.kind })}
          </AlertDialogTitle>
          <AlertDialogDescription>{t("description")}</AlertDialogDescription>
        </AlertDialogHeader>

        {loading && !revealed ? (
          <Skeleton className="h-32 w-full" />
        ) : revealed ? (
          <div className="space-y-3">
            <div className="bg-muted/40 rounded-md border p-3 text-xs">
              <p className="text-muted-foreground">{t("secretRefLabel")}</p>
              <p className="text-foreground mt-0.5 font-mono break-all">
                {revealed.connectionSecretRef || t("noSecretRef")}
              </p>
              <p className="text-muted-foreground text-2xs mt-2">
                {t("revealedAt", { when: fmt.formatRelativeTime(revealed.revealedAt) })}
              </p>
            </div>
            <ul className="space-y-1.5">
              {revealed.keys.map((k) => (
                <li
                  key={k.key}
                  className="flex items-center gap-2 rounded-md border bg-transparent p-2 font-mono text-xs"
                >
                  <span className="text-foreground shrink-0">{k.key}</span>
                  <span className="text-muted-foreground flex-1 truncate">
                    {k.isSecret ? "••••" : k.value}
                  </span>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="size-7"
                    onClick={() => handleCopy(k.value)}
                  >
                    <CopyIcon className="size-3.5" />
                    <span className="sr-only">{t("copy")}</span>
                  </Button>
                </li>
              ))}
            </ul>
            <p className="text-muted-foreground text-2xs">{t("plaintextWarning")}</p>
          </div>
        ) : (
          <p className="text-muted-foreground text-xs">{t("loading")}</p>
        )}

        <AlertDialogFooter>
          <AlertDialogCancel>{t("close")}</AlertDialogCancel>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

// ─── send test email dialog ─────────────────────────────────────────

function SendTestEmailDialog({
  svc,
  open,
  onOpenChange,
}: {
  svc: AstroliftManagedService;
  open: boolean;
  onOpenChange: (next: boolean) => void;
}) {
  const t = useTranslations("apps.settings.managedServicesSummary.sendEmailDialog");
  const [recipient, setRecipient] = React.useState("");
  const [subject, setSubject] = React.useState("");
  const [body, setBody] = React.useState("");
  const [send, { loading }] = useMutation<SendTestEmailResp>(SEND_MANAGED_SERVICE_TEST_EMAIL);

  React.useEffect(() => {
    if (!open) {
      setRecipient("");
      setSubject("");
      setBody("");
    }
  }, [open]);

  async function handleSend() {
    if (!recipient.trim()) return;
    try {
      const { data } = await send({
        variables: {
          input: {
            managedServiceId: svc.id,
            recipient: recipient.trim(),
            subject: subject.trim() || null,
            body: body.trim() || null,
          },
        },
      });
      const env = data?.sendManagedServiceTestEmail;
      if (!env) {
        toast.error(t("noResponse"));
        return;
      }
      if (!env.ok) {
        toast.error(env.errors[0]?.message ?? t("failed"));
        return;
      }
      const payload = env.data;
      if (!payload) {
        toast.error(t("noPayload"));
        return;
      }
      toast.success(t("sent", { recipient: payload.recipient, transport: payload.transport }));
      onOpenChange(false);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("failed"));
    }
  }

  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle className="flex items-center gap-2">
            <SendIcon className="size-4" />
            {t("title", { name: svc.name || svc.kind })}
          </AlertDialogTitle>
          <AlertDialogDescription>{t("description")}</AlertDialogDescription>
        </AlertDialogHeader>
        <div className="space-y-3 py-2">
          <div className="grid gap-1.5">
            <Label htmlFor="msvc-test-recipient" className="text-xs">
              {t("recipientLabel")}
            </Label>
            <Input
              id="msvc-test-recipient"
              type="email"
              value={recipient}
              onChange={(e) => setRecipient(e.target.value)}
              placeholder="qa@example.com"
              autoComplete="off"
              spellCheck={false}
              required
            />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="msvc-test-subject" className="text-xs">
              {t("subjectLabel")}
            </Label>
            <Input
              id="msvc-test-subject"
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              placeholder={t("subjectPlaceholder")}
              spellCheck={false}
            />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="msvc-test-body" className="text-xs">
              {t("bodyLabel")}
            </Label>
            <Textarea
              id="msvc-test-body"
              value={body}
              onChange={(e) => setBody(e.target.value)}
              placeholder={t("bodyPlaceholder")}
              rows={3}
            />
          </div>
          <p className="text-muted-foreground text-2xs">{t("hint")}</p>
        </div>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={loading}>{t("cancel")}</AlertDialogCancel>
          <AlertDialogAction
            disabled={loading || !recipient.trim()}
            onClick={(e) => {
              e.preventDefault();
              void handleSend();
            }}
          >
            {loading ? (
              <Loader2Icon className="size-4 animate-spin" />
            ) : (
              <SendIcon className="size-4" />
            )}
            {t("send")}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

// ─── list objects dialog ────────────────────────────────────────────

function ListObjectsDialog({
  svc,
  open,
  onOpenChange,
}: {
  svc: AstroliftManagedService;
  open: boolean;
  onOpenChange: (next: boolean) => void;
}) {
  const t = useTranslations("apps.settings.managedServicesSummary.objectsDialog");
  const fmt = useFormatters();
  const [load, query] = useLazyQuery<ObjectsResp>(LIST_MANAGED_SERVICE_OBJECTS, {
    fetchPolicy: "cache-and-network",
  });

  React.useEffect(() => {
    if (open) {
      void load({ variables: { managedServiceId: svc.id, limit: 10 } });
    }
  }, [open, load, svc.id]);

  const result =
    (query.data?.astroliftManagedServiceObjects as AstroliftManagedServiceObjects | undefined) ??
    null;
  const loading = query.loading && !result;
  const objects = result?.objects ?? [];

  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent className="max-h-[85vh] overflow-y-auto">
        <AlertDialogHeader>
          <AlertDialogTitle className="flex items-center gap-2">
            <HardDriveIcon className="size-4" />
            {t("title", { name: svc.name || svc.kind })}
          </AlertDialogTitle>
          <AlertDialogDescription>{t("description")}</AlertDialogDescription>
        </AlertDialogHeader>
        {loading ? (
          <Skeleton className="h-32 w-full" />
        ) : objects.length === 0 ? (
          <div className="bg-muted/40 rounded-md border p-4 text-xs">
            <p className="text-muted-foreground">{t("empty")}</p>
          </div>
        ) : (
          <div className="space-y-2">
            <ul className="space-y-1">
              {objects.map((o) => (
                <li
                  key={o.key}
                  className="flex items-center gap-2 rounded-md border bg-transparent p-2 font-mono text-xs"
                >
                  <span className="text-foreground flex-1 truncate">{o.key}</span>
                  <span className="text-muted-foreground shrink-0">{formatBytes(o.sizeBytes)}</span>
                  {o.lastModified ? (
                    <span className="text-muted-foreground text-2xs shrink-0">
                      {fmt.formatRelativeTime(o.lastModified)}
                    </span>
                  ) : null}
                </li>
              ))}
            </ul>
            <p className="text-muted-foreground text-2xs">
              {result?.cacheAgeSeconds != null
                ? t("cacheAge", { age: formatSecondsShort(result.cacheAgeSeconds) })
                : t("noCacheAge")}
              {result?.truncated ? ` · ${t("truncated")}` : ""}
            </p>
          </div>
        )}
        <AlertDialogFooter>
          <AlertDialogAction
            asChild
            onClick={() => {
              void load({ variables: { managedServiceId: svc.id, limit: 10 } });
            }}
          >
            <Button variant="outline" size="sm">
              <RefreshCwIcon className="size-3.5" />
              {t("refresh")}
            </Button>
          </AlertDialogAction>
          <AlertDialogCancel>{t("close")}</AlertDialogCancel>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

// ─── queue depth dialog ─────────────────────────────────────────────

function QueueDepthDialog({
  svc,
  open,
  onOpenChange,
}: {
  svc: AstroliftManagedService;
  open: boolean;
  onOpenChange: (next: boolean) => void;
}) {
  const t = useTranslations("apps.settings.managedServicesSummary.depthDialog");
  const fmt = useFormatters();
  const [load, query] = useLazyQuery<QueueDepthResp>(GET_MANAGED_SERVICE_QUEUE_DEPTH, {
    fetchPolicy: "cache-and-network",
  });

  React.useEffect(() => {
    if (open) {
      void load({ variables: { managedServiceId: svc.id } });
    }
  }, [open, load, svc.id]);

  const result =
    (query.data?.astroliftManagedServiceQueueDepth as
      | AstroliftManagedServiceQueueDepth
      | undefined) ?? null;
  const loading = query.loading && !result;

  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle className="flex items-center gap-2">
            <GaugeIcon className="size-4" />
            {t("title", { name: svc.name || svc.kind })}
          </AlertDialogTitle>
          <AlertDialogDescription>{t("description")}</AlertDialogDescription>
        </AlertDialogHeader>
        {loading ? (
          <Skeleton className="h-20 w-full" />
        ) : result ? (
          <div className="grid grid-cols-2 gap-3">
            <div className="rounded-md border p-3 text-center">
              <p className="text-muted-foreground text-xs">{t("depth")}</p>
              <p className="text-foreground mt-1 text-2xl font-semibold">{result.depth}</p>
            </div>
            <div className="rounded-md border p-3 text-center">
              <p className="text-muted-foreground text-xs">{t("inFlight")}</p>
              <p className="text-foreground mt-1 text-2xl font-semibold">{result.inFlight}</p>
            </div>
            <p className="text-muted-foreground text-2xs col-span-2">
              {result.sampledAt
                ? t("sampledAt", { when: fmt.formatRelativeTime(result.sampledAt) })
                : t("noSnapshot")}
            </p>
          </div>
        ) : (
          <p className="text-muted-foreground text-xs">{t("loading")}</p>
        )}
        <AlertDialogFooter>
          <AlertDialogAction
            asChild
            onClick={() => {
              void load({ variables: { managedServiceId: svc.id } });
            }}
          >
            <Button variant="outline" size="sm">
              <RefreshCwIcon className="size-3.5" />
              {t("refresh")}
            </Button>
          </AlertDialogAction>
          <AlertDialogCancel>{t("close")}</AlertDialogCancel>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
