"use client";

import {
  AlertTriangleIcon,
  BoxIcon,
  CheckIcon,
  CopyIcon,
  DownloadIcon,
  ExternalLinkIcon,
  FileCode2Icon,
  LayersIcon,
  Loader2Icon,
  TerminalIcon,
  UploadIcon,
  XIcon,
} from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import { EmptyState } from "@/components/EmptyState";
import { TerminalEmulator } from "@/components/observability";
import { PageShell } from "@/components/PageShell";
import { Panel, PanelGrid } from "@/components/panel/Panel";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import type { AstroliftRegisteredApp } from "@/graphql/registry/registry.types";

import type { UploadedScript } from "./use-app-shell";

export interface ShellScreenProps {
  slug: string;
  app: Pick<AstroliftRegisteredApp, "name" | "slug"> | null;
  loading: boolean;
  // Pod + container target (usePodTarget).
  podRows: { name: string }[];
  selectedPod: string | null;
  setPickedPod: (pod: string) => void;
  podContainers: string[];
  selectedContainer: string | null;
  setPickedContainer: (container: string | null) => void;
  podsLoading: boolean;
  noPods: boolean;
  // Script upload (#673).
  uploading: boolean;
  uploadedFile: UploadedScript | null;
  uploadedCommand: string | null;
  fetchCommand: string | null;
  onPickScript: (file: File) => Promise<void>;
  onClearUpload: () => void;
  /** Chrome-aware links: the deployments empty-state action and the tokens page. */
  deploymentsHref: string;
  tokensHref: string;
  /** The app tab bar. */
  tabs?: React.ReactNode;
}

/**
 * Logs & metrics › Console (spec 44 §5.2): the acting half of what used to be
 * the Console tab (#1247). An interactive root shell on a running pod, the
 * script upload that feeds it, and the paste-ready CLI equivalents, each a
 * Panel. The Logs section next door stays read-only.
 */
export function ShellScreen({
  slug,
  app: a,
  loading,
  podRows,
  selectedPod,
  setPickedPod,
  podContainers,
  selectedContainer,
  setPickedContainer,
  podsLoading,
  noPods,
  uploading,
  uploadedFile,
  uploadedCommand,
  fetchCommand,
  onPickScript,
  onClearUpload,
  deploymentsHref,
  tokensHref,
  tabs,
}: ShellScreenProps) {
  const tCommon = useTranslations("apps.common");
  const t = useTranslations("apps.shell");
  const tObs = useTranslations("apps.observability");

  // Operator opts into the WS exec connection — keeps an idle shell tab from
  // holding a kubelet exec socket open just because someone clicked through
  // while triaging.
  const [shellOpen, setShellOpen] = React.useState(false);
  // Reset the open shell when the pod/container picker changes so the next
  // click starts a clean session against the new target.
  const shellKey = `${selectedPod ?? ""}::${selectedContainer ?? ""}`;
  const [prevShellKey, setPrevShellKey] = React.useState(shellKey);
  if (prevShellKey !== shellKey) {
    setPrevShellKey(shellKey);
    if (shellOpen) setShellOpen(false);
  }

  const uploadInputRef = React.useRef<HTMLInputElement | null>(null);

  // #674 — prefilled command palette. Each shortcut is a frequent operator
  // action; clicking the row copies the command. Grouped by intent (shell /
  // runtime / observability / lifecycle) but rendered flat for now since the
  // list is short. Templates take the app slug so the command is paste-ready
  // for the current app.
  const sampleCommands = React.useMemo(
    () => [
      // Interactive shell + REPL — fastest way to land inside a pod
      {
        key: "openShell" as const,
        label: t("shortcuts.openShell"),
        template: (s: string) => `astro exec --app=${s} -- bash`,
      },
      {
        key: "djangoShell" as const,
        label: "Open Django shell (Python)",
        template: (s: string) => `astro exec --app=${s} -- python manage.py shell`,
      },
      {
        key: "dbShell" as const,
        label: "Open psql against DATABASE_URL",
        template: (s: string) => `astro exec --app=${s} -- bash -lc 'psql "$DATABASE_URL"'`,
      },
      {
        key: "redisShell" as const,
        label: "Open redis-cli against REDIS_URL",
        template: (s: string) => `astro exec --app=${s} -- bash -lc 'redis-cli -u "$REDIS_URL"'`,
      },
      // Migrations — Django and Flask/Alembic variants
      {
        key: "djangoCmd" as const,
        label: t("shortcuts.djangoCmd"),
        template: (s: string) => `astro exec --app=${s} -- python manage.py migrate --check`,
      },
      {
        key: "djangoMigrate" as const,
        label: "Run Django migrations",
        template: (s: string) => `astro exec --app=${s} -- python manage.py migrate --noinput`,
      },
      {
        key: "flaskUpgrade" as const,
        label: "Run Flask / Alembic upgrade",
        template: (s: string) => `astro exec --app=${s} -- flask db upgrade`,
      },
      // Observability — logs + recent deploys + env
      {
        key: "tailLogs" as const,
        label: t("shortcuts.tailLogs"),
        template: (s: string) => `astro logs --app=${s} --follow`,
      },
      {
        key: "recentLogs" as const,
        label: "Last 200 log lines",
        template: (s: string) => `astro logs --app=${s} --tail=200`,
      },
      {
        key: "envDump" as const,
        label: "Dump pod environment (debug missing var)",
        template: (s: string) => `astro exec --app=${s} -- bash -lc 'env | sort'`,
      },
      // Lifecycle — deploy + rollback
      {
        key: "deploy" as const,
        label: "Trigger a deploy",
        template: (s: string) => `astro deploy --app=${s}`,
      },
      {
        key: "rollback" as const,
        label: "Rollback to previous deploy",
        template: (s: string) => `astro deploy --app=${s} --rollback`,
      },
    ],
    [t]
  );

  if (loading) {
    return (
      <PageShell title={tCommon("loading")}>
        <Skeleton className="h-32 w-full" />
      </PageShell>
    );
  }

  if (!a) {
    return (
      <PageShell title={tCommon("notFound")}>
        <EmptyState
          icon={<AlertTriangleIcon className="size-5" />}
          title={tCommon("notFoundSlug", { slug })}
          description={tCommon("notFoundDescription")}
          actionHref="/apps"
          actionLabel={tCommon("backToApps")}
        />
      </PageShell>
    );
  }

  return (
    <PageShell
      title={t("title", { name: a.name })}
      description={
        <span className="text-muted-foreground font-mono text-xs">
          {t("description", { slug: a.slug })}
        </span>
      }
    >
      {tabs}

      <PanelGrid>
        <Panel
          title={t("terminal.title")}
          icon={<TerminalIcon className="size-4" />}
          description={
            selectedPod && selectedContainer ? (
              <span className="font-mono">
                {t("terminal.targetDescription", {
                  pod: selectedPod,
                  container: selectedContainer,
                })}
              </span>
            ) : (
              t("terminal.pickTarget")
            )
          }
          actions={
            noPods ? null : shellOpen ? (
              <Button size="sm" variant="outline" onClick={() => setShellOpen(false)}>
                {t("terminal.close")}
              </Button>
            ) : (
              <Button
                size="sm"
                onClick={() => setShellOpen(true)}
                disabled={!selectedPod || !selectedContainer}
              >
                <TerminalIcon className="size-3.5" />
                {t("terminal.open")}
              </Button>
            )
          }
          loading={podsLoading}
          empty={
            noPods
              ? {
                  icon: <BoxIcon className="size-5" />,
                  title: tObs("pods.emptyTitle"),
                  description: tObs("pods.emptyDescription"),
                  actionHref: deploymentsHref,
                  actionLabel: tObs("pods.emptyAction"),
                }
              : null
          }
        >
          <div className="flex min-w-0 flex-col gap-4">
            {/* Each surface owns its target: the pod and container the shell opens on. */}
            <div className="flex min-w-0 flex-wrap items-center gap-2">
              <div className="flex min-w-0 items-center gap-1.5">
                <BoxIcon aria-hidden="true" className="text-muted-foreground size-3.5 shrink-0" />
                <Select value={selectedPod ?? ""} onValueChange={(v) => setPickedPod(v)}>
                  <SelectTrigger
                    size="sm"
                    aria-label={t("podLabel")}
                    className="max-w-full min-w-0 font-mono text-xs"
                  >
                    <SelectValue placeholder={t("podPlaceholder")} />
                  </SelectTrigger>
                  <SelectContent>
                    {podRows.map((p) => (
                      <SelectItem key={p.name} value={p.name} className="font-mono">
                        {p.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="flex min-w-0 items-center gap-1.5">
                <LayersIcon
                  aria-hidden="true"
                  className="text-muted-foreground size-3.5 shrink-0"
                />
                <Select
                  value={selectedContainer ?? ""}
                  onValueChange={(v) => setPickedContainer(v)}
                  disabled={podContainers.length === 0}
                >
                  <SelectTrigger
                    size="sm"
                    aria-label={t("containerLabel")}
                    className="max-w-full min-w-0 font-mono text-xs"
                  >
                    <SelectValue placeholder={t("containerPlaceholder")} />
                  </SelectTrigger>
                  <SelectContent>
                    {podContainers.map((c) => (
                      <SelectItem key={c} value={c} className="font-mono">
                        {c}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>

            {shellOpen && selectedPod && selectedContainer ? (
              <TerminalEmulator
                appSlug={a.slug}
                podName={selectedPod}
                container={selectedContainer}
              />
            ) : (
              <div className="bg-muted/40 flex h-64 items-center justify-center rounded-md border border-dashed">
                <div className="text-muted-foreground flex min-w-0 flex-col items-center gap-2 px-4 text-center">
                  <TerminalIcon className="size-8" />
                  <p className="text-sm">
                    {selectedPod ? t("terminal.readyHint") : t("terminal.pickTarget")}
                  </p>
                  <p className="max-w-md text-xs">
                    {t.rich("terminal.untilThen", {
                      cli: () => (
                        <code className="bg-background rounded px-1 py-0.5 font-mono">astro</code>
                      ),
                    })}
                  </p>
                </div>
              </div>
            )}
            <div className="flex flex-wrap gap-2">
              <Button asChild variant="outline" size="sm">
                <Link href="/downloads">
                  <DownloadIcon className="size-4" />
                  {t("terminal.install")}
                </Link>
              </Button>
              <Button asChild variant="outline" size="sm">
                <Link href={tokensHref}>
                  <ExternalLinkIcon className="size-4" />
                  {t("terminal.manageTokens")}
                </Link>
              </Button>
            </div>
          </div>
        </Panel>

        <Panel
          span={6}
          title={t("upload.title")}
          icon={<FileCode2Icon className="size-4" />}
          description={t("upload.description")}
          actions={
            <>
              <input
                ref={uploadInputRef}
                type="file"
                accept=".py,.sql,.sh,.ts,.js,.rb,.go,text/plain"
                className="hidden"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) void onPickScript(f);
                  // Reset so re-uploading the same filename refires onChange.
                  e.target.value = "";
                }}
              />
              <Button
                size="sm"
                variant="outline"
                onClick={() => uploadInputRef.current?.click()}
                disabled={uploading}
              >
                {uploading ? (
                  <Loader2Icon className="size-3.5 animate-spin" />
                ) : (
                  <UploadIcon className="size-3.5" />
                )}
                {uploading ? t("upload.uploading") : t("upload.pick")}
              </Button>
            </>
          }
        >
          {uploadedFile ? (
            <div className="flex min-w-0 flex-col gap-3">
              <div className="flex min-w-0 flex-wrap items-center gap-2">
                <Badge
                  variant="secondary"
                  className="max-w-full min-w-0 font-mono text-xs [overflow-wrap:anywhere] whitespace-normal"
                >
                  {uploadedFile.name}
                  <button
                    type="button"
                    aria-label={t("upload.clear")}
                    onClick={onClearUpload}
                    className="hover:bg-background/60 ml-1 shrink-0 rounded-sm p-0.5"
                  >
                    <XIcon className="size-3" />
                  </button>
                </Badge>
                <span className="text-muted-foreground min-w-0 font-mono text-xs [overflow-wrap:anywhere]">
                  {t("upload.path", { path: `/tmp/${uploadedFile.name}` })}
                </span>
              </div>
              {uploadedCommand && (
                <CopyableCommand label={t("upload.runLabel")} command={uploadedCommand} />
              )}
              {fetchCommand && (
                <CopyableCommand label={t("upload.fetchLabel")} command={fetchCommand} />
              )}
              <p className="text-muted-foreground text-xs">{t("upload.note")}</p>
            </div>
          ) : (
            <div className="text-muted-foreground bg-muted/40 rounded-md border border-dashed p-4 text-center text-xs">
              {t("upload.empty")}
            </div>
          )}
        </Panel>

        <Panel
          span={6}
          title={t("shortcuts.title")}
          description={t.rich("shortcuts.description", {
            login: () => <code className="font-mono">astro login</code>,
          })}
        >
          <div className="flex min-w-0 flex-col gap-3">
            {sampleCommands.map((s) => (
              <CopyableCommand key={s.key} label={s.label} command={s.template(a.slug)} />
            ))}
          </div>
        </Panel>
      </PanelGrid>
    </PageShell>
  );
}

function CopyableCommand({ label, command }: { label: string; command: string }) {
  return (
    <div className="min-w-0">
      <div className="text-muted-foreground mb-1 text-xs tracking-wide uppercase">{label}</div>
      <div className="bg-muted flex min-w-0 items-start gap-2 rounded-md p-2">
        <pre className="min-w-0 flex-1 font-mono text-xs leading-relaxed [overflow-wrap:anywhere] whitespace-pre-wrap">
          {command}
        </pre>
        <CopyButton value={command} />
      </div>
    </div>
  );
}

function CopyButton({ value }: { value: string }) {
  const t = useTranslations("apps.shell");
  const [copied, setCopied] = React.useState(false);
  return (
    <Button
      type="button"
      variant="ghost"
      size="icon"
      className="size-7 shrink-0"
      aria-label={t("copy")}
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(value);
          setCopied(true);
          toast.success(t("copied"));
          setTimeout(() => setCopied(false), 1200);
        } catch {
          toast.error(t("copyFailed"));
        }
      }}
    >
      {copied ? <CheckIcon className="size-3.5" /> : <CopyIcon className="size-3.5" />}
    </Button>
  );
}
