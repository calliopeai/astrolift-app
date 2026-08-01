"use client";

import { useMutation, useQuery } from "@apollo/client/react";
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
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { GET_APP } from "@/graphql/registry/registry.queries";
import type { AstroliftRegisteredApp } from "@/graphql/registry/registry.types";
import { UPLOAD_FILE } from "@/graphql/uploads/uploads.mutations";
import type { FileUploadResult } from "@/graphql/__generated__/schema";

import { appPath, useAppChrome } from "../components/app-chrome-context";
import { AppTabs } from "../components/app-tabs";
import { usePodTarget } from "../components/use-pod-target";

interface AppResp {
  astroliftApp: AstroliftRegisteredApp | null;
}

/**
 * Control › Shell — the acting half of what used to be the Console tab
 * (#1247). An interactive root shell on a running pod, the script upload that
 * feeds it, and the paste-ready CLI equivalents. Observability sits next door
 * and stays read-only.
 */
export function ShellClient({ slug }: { slug: string }) {
  const chrome = useAppChrome();
  const tCommon = useTranslations("apps.common");
  const t = useTranslations("apps.shell");
  const tObs = useTranslations("apps.observability");

  const app = useQuery<AppResp>(GET_APP, { variables: { slug } });
  const a = app.data?.astroliftApp;

  const {
    podRows,
    selectedPod,
    setPickedPod,
    podContainers,
    selectedContainer,
    setPickedContainer,
    podsLoading,
    noPods,
  } = usePodTarget(slug);

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

  // #673 — script upload. Operator picks a .py/.sh/.sql/etc. file; we mint a
  // pre-signed PUT URL via fileUpload, push the bytes to object storage, and
  // surface the public URL + a paste-ready `curl`/run pair so the next
  // `astro exec` lands the file at /tmp and runs it.
  const [uploadFile, uploadState] = useMutation<{ fileUpload: FileUploadResult }>(UPLOAD_FILE);
  const [uploadedFile, setUploadedFile] = React.useState<{
    name: string;
    publicUrl: string;
  } | null>(null);
  const [putInFlight, setPutInFlight] = React.useState(false);
  const uploadInputRef = React.useRef<HTMLInputElement | null>(null);
  const uploading = uploadState.loading || putInFlight;

  const onPickScript = async (file: File) => {
    setUploadedFile(null);
    setPutInFlight(false);
    const mimetype = file.type || "text/plain";
    try {
      const res = await uploadFile({ variables: { mimetype, name: file.name } });
      const result = res.data?.fileUpload;
      const url = result?.preSignedUrl;
      const publicUrl = result?.publicUrl;
      if (!url || !publicUrl) {
        toast.error(t("upload.failed", { error: t("upload.noUrl") }));
        return;
      }
      setPutInFlight(true);
      const put = await fetch(url, {
        method: "PUT",
        body: file,
        headers: { "Content-Type": mimetype },
      });
      if (!put.ok) {
        toast.error(t("upload.failed", { error: `HTTP ${put.status}` }));
        return;
      }
      setUploadedFile({ name: file.name, publicUrl });
      toast.success(t("upload.success", { name: file.name }));
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      toast.error(t("upload.failed", { error: msg }));
    } finally {
      setPutInFlight(false);
    }
  };

  const uploadedCommand = React.useMemo(() => {
    if (!uploadedFile) return null;
    const name = uploadedFile.name;
    const lower = name.toLowerCase();
    const path = `/tmp/${name}`;
    if (lower.endsWith(".py")) return `python ${path}`;
    if (lower.endsWith(".sh")) return `bash ${path}`;
    if (lower.endsWith(".sql")) return `psql "$DATABASE_URL" < ${path}`;
    if (lower.endsWith(".rb")) return `ruby ${path}`;
    if (lower.endsWith(".js")) return `node ${path}`;
    if (lower.endsWith(".ts")) return `npx tsx ${path}`;
    if (lower.endsWith(".go")) return `go run ${path}`;
    return path;
  }, [uploadedFile]);

  const fetchCommand = React.useMemo(() => {
    if (!uploadedFile) return null;
    return `curl -fsSL -o /tmp/${uploadedFile.name} '${uploadedFile.publicUrl}'`;
  }, [uploadedFile]);

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

  if (app.loading && !a) {
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
      <AppTabs slug={a.slug} />

      <Card>
        <CardHeader className="flex flex-row items-start justify-between gap-3 space-y-0 pb-3">
          <div>
            <CardTitle className="flex items-center gap-2 text-base">
              <TerminalIcon className="size-4" />
              {t("terminal.title")}
            </CardTitle>
            <CardDescription>
              {selectedPod && selectedContainer
                ? t("terminal.targetDescription", {
                    pod: selectedPod,
                    container: selectedContainer,
                  })
                : t("terminal.pickTarget")}
            </CardDescription>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {/* Pod + container pickers live here now: the Console tab hosted
                them in the log card's header, and the shell borrowed the
                selection. Split apart, each surface owns its own target. */}
            <div className="flex items-center gap-1.5">
              <BoxIcon aria-hidden="true" className="text-muted-foreground size-3.5" />
              <Select
                value={selectedPod ?? ""}
                onValueChange={(v) => setPickedPod(v)}
                disabled={podsLoading || noPods}
              >
                <SelectTrigger size="sm" aria-label={t("podLabel")} className="font-mono text-xs">
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
            <div className="flex items-center gap-1.5">
              <LayersIcon aria-hidden="true" className="text-muted-foreground size-3.5" />
              <Select
                value={selectedContainer ?? ""}
                onValueChange={(v) => setPickedContainer(v)}
                disabled={podContainers.length === 0}
              >
                <SelectTrigger
                  size="sm"
                  aria-label={t("containerLabel")}
                  className="font-mono text-xs"
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
            {shellOpen ? (
              <Button size="sm" variant="outline" onClick={() => setShellOpen(false)}>
                {t("terminal.close")}
              </Button>
            ) : (
              <Button
                size="sm"
                onClick={() => setShellOpen(true)}
                disabled={!selectedPod || !selectedContainer}
              >
                <TerminalIcon className="size-3" />
                {t("terminal.open")}
              </Button>
            )}
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          {noPods ? (
            <EmptyState
              icon={<BoxIcon className="size-5" />}
              title={tObs("pods.emptyTitle")}
              description={tObs("pods.emptyDescription")}
              actionHref={appPath(chrome, a.slug, "deployments")}
              actionLabel={tObs("pods.emptyAction")}
            />
          ) : shellOpen && selectedPod && selectedContainer ? (
            <TerminalEmulator
              appSlug={a.slug}
              podName={selectedPod}
              container={selectedContainer}
            />
          ) : (
            <div className="bg-muted/40 flex h-64 items-center justify-center rounded-md border border-dashed">
              <div className="text-muted-foreground flex flex-col items-center gap-2 px-4 text-center">
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
            <Button asChild variant="outline">
              <Link href="/downloads">
                <DownloadIcon className="size-4" />
                {t("terminal.install")}
              </Link>
            </Button>
            <Button asChild variant="outline">
              <Link href={appPath(chrome, a.slug, "tokens")}>
                <ExternalLinkIcon className="size-4" />
                {t("terminal.manageTokens")}
              </Link>
            </Button>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-row items-start justify-between gap-3 space-y-0 pb-3">
          <div>
            <CardTitle className="flex items-center gap-2 text-base">
              <FileCode2Icon className="size-4" />
              {t("upload.title")}
            </CardTitle>
            <CardDescription>{t("upload.description")}</CardDescription>
          </div>
          <div className="flex items-center gap-2">
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
                <Loader2Icon className="size-3 animate-spin" />
              ) : (
                <UploadIcon className="size-3" />
              )}
              {uploading ? t("upload.uploading") : t("upload.pick")}
            </Button>
          </div>
        </CardHeader>
        <CardContent className="space-y-3">
          {uploadedFile ? (
            <>
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant="secondary" className="font-mono text-xs">
                  {uploadedFile.name}
                  <button
                    type="button"
                    aria-label={t("upload.clear")}
                    onClick={() => setUploadedFile(null)}
                    className="hover:bg-background/60 ml-1 rounded-sm p-0.5"
                  >
                    <XIcon className="size-3" />
                  </button>
                </Badge>
                <span className="text-muted-foreground text-xs">
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
            </>
          ) : (
            <div className="text-muted-foreground bg-muted/40 rounded-md border border-dashed p-4 text-center text-xs">
              {t("upload.empty")}
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">{t("shortcuts.title")}</CardTitle>
          <CardDescription>
            {t.rich("shortcuts.description", {
              login: () => (
                <code className="bg-muted rounded px-1 py-0.5 font-mono">astro login</code>
              ),
            })}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {sampleCommands.map((s) => (
            <CopyableCommand key={s.key} label={s.label} command={s.template(a.slug)} />
          ))}
        </CardContent>
      </Card>
    </PageShell>
  );
}

function CopyableCommand({ label, command }: { label: string; command: string }) {
  return (
    <div>
      <div className="text-muted-foreground mb-1 text-xs tracking-wide uppercase">{label}</div>
      <div className="bg-muted flex items-start gap-2 rounded-md p-2">
        <pre className="flex-1 overflow-x-auto font-mono text-xs leading-relaxed break-all whitespace-pre-wrap">
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
