"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import {
  CheckCircle2Icon,
  DownloadIcon,
  ExternalLinkIcon,
  FileBoxIcon,
  ScanLineIcon,
  ShieldCheckIcon,
  ShieldOffIcon,
} from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";

import { Can } from "@/components/Can";
import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { LIST_EVENTS } from "@/graphql/operations/operations.queries";
import { UPDATE_SECURITY_POLICY } from "@/graphql/registry/registry.mutations";
import { GET_APP } from "@/graphql/registry/registry.queries";
import type {
  AstroliftRegisteredAppMutationResult,
  AstroliftSecurityPolicy,
} from "@/graphql/__generated__/operations";
import type { AstroliftRegisteredApp } from "@/graphql/registry/registry.types";

import { AppTabs } from "../components/app-tabs";

const DEFAULT_POLICY = {
  blockOnCriticalCves: true,
  blockOnMissingSignature: true,
  blockOnHighCveThreshold: null as number | null,
};

interface AppResp {
  astroliftApp: AstroliftRegisteredApp | null;
}

interface EventsResp {
  astroliftEvents: AstroliftEvent[];
}

interface AstroliftEvent {
  id: string;
  eventType: string;
  payload: unknown;
  organizationId?: string | null;
  teamId?: string | null;
  projectId?: string | null;
  registeredAppId?: string | null;
  occurredAt: string;
}

interface SigningPayload {
  image_digest?: string;
  image_tag?: string;
  signer_identity?: string;
  rekor_log_index?: number;
  rekor_entry_url?: string;
  signed_at?: string;
}

interface SbomPayload {
  image_digest?: string;
  format?: string;
  artifact_url?: string;
  component_count?: number;
  generated_at?: string;
}

interface ScanFinding {
  cve_id: string;
  severity: "critical" | "high" | "medium" | "low";
  package_name: string;
  package_version: string;
  fixed_in_version?: string | null;
  description?: string | null;
}

interface ScanPayload {
  image_digest?: string;
  scanned_at?: string;
  counts?: { critical: number; high: number; medium: number; low: number };
  findings?: ScanFinding[];
}

const SEVERITY_TONE: Record<ScanFinding["severity"], string> = {
  critical: "bg-red-500/15 text-red-700 dark:text-red-300",
  high: "bg-orange-500/15 text-orange-700 dark:text-orange-300",
  medium: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
  low: "bg-slate-500/15 text-slate-700 dark:text-slate-300",
};

function formatTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString();
}

function asObject(payload: unknown): Record<string, unknown> | null {
  if (typeof payload === "object" && payload !== null && !Array.isArray(payload)) {
    return payload as Record<string, unknown>;
  }
  return null;
}

export function AppSecurityClient({ slug }: { slug: string }) {
  const tCommon = useTranslations("apps.common");
  const t = useTranslations("apps.security");
  const app = useQuery<AppResp>(GET_APP, { variables: { slug } });
  // Org-wide events query — we filter client-side by registeredAppId
  // until astroliftEvents grows an appSlug arg per #313.
  const events = useQuery<EventsResp>(LIST_EVENTS, {
    variables: { limit: 100 },
    fetchPolicy: "cache-and-network",
    pollInterval: 60000,
  });

  const a = app.data?.astroliftApp ?? null;
  const allEvents = events.data?.astroliftEvents ?? [];

  const appEvents = React.useMemo(() => {
    if (!a) return [];
    return allEvents.filter((e) => e.registeredAppId === a.id);
  }, [allEvents, a]);

  const latestSigning = React.useMemo(() => {
    return appEvents.find((e) => e.eventType === "image.signed") ?? null;
  }, [appEvents]);

  const latestScan = React.useMemo(() => {
    return appEvents.find((e) => e.eventType === "image.scanned") ?? null;
  }, [appEvents]);

  const latestSbom = React.useMemo(() => {
    return appEvents.find((e) => e.eventType === "sbom.generated") ?? null;
  }, [appEvents]);

  if (app.loading && !a) {
    return (
      <PageShell title={t("loadingTitle")} description={tCommon("loading")}>
        <Skeleton className="h-32 w-full" />
      </PageShell>
    );
  }

  if (!a) {
    return (
      <PageShell
        title={tCommon("notFound")}
        description={tCommon("notFoundPermission")}
      >
        <EmptyState
          icon={<ShieldCheckIcon className="size-5" />}
          title={tCommon("notFoundSlug", { slug })}
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
      <AppTabs slug={a.slug} active="security" />

      <SigningCard event={latestSigning} loading={events.loading && !events.data} />
      <SbomCard event={latestSbom} loading={events.loading && !events.data} />
      <ScanCard event={latestScan} loading={events.loading && !events.data} />
      <PolicyCard appSlug={a.slug} initialPolicy={a.securityPolicy ?? DEFAULT_POLICY} />
    </PageShell>
  );
}

function SigningCard({ event, loading }: { event: AstroliftEvent | null; loading: boolean }) {
  const t = useTranslations("apps.security.signing");
  const payload = (event && asObject(event.payload)) as SigningPayload | null;
  const hasEvent = event !== null && payload !== null;

  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-3 space-y-0 pb-3">
        <div className="flex items-start gap-3">
          <ShieldCheckIcon className="mt-0.5 size-4 text-emerald-700 dark:text-emerald-300" />
          <div>
            <CardTitle className="text-sm">{t("title")}</CardTitle>
            <CardDescription>{t("description")}</CardDescription>
          </div>
        </div>
        {hasEvent ? (
          <Badge className="bg-emerald-500/15 text-emerald-700 dark:text-emerald-300">
            {t("signed")}
          </Badge>
        ) : (
          <Badge variant="outline">{t("noData")}</Badge>
        )}
      </CardHeader>
      <CardContent className="space-y-3">
        {loading ? (
          <Skeleton className="h-16 w-full" />
        ) : !hasEvent ? (
          <p className="text-muted-foreground text-sm">{t("noEvent")}</p>
        ) : (
          <dl className="grid grid-cols-1 gap-x-8 gap-y-2 text-sm sm:grid-cols-2">
            <Field label={t("imageTag")} mono value={payload.image_tag || "—"} />
            <Field
              label={t("signedAt")}
              value={formatTime(payload.signed_at ?? event?.occurredAt ?? null)}
            />
            <Field
              label={t("signerIdentity")}
              mono
              value={payload.signer_identity || "—"}
            />
            <Field
              label={t("imageDigest")}
              mono
              value={
                payload.image_digest ? (
                  <span className="break-all">{payload.image_digest}</span>
                ) : (
                  "—"
                )
              }
            />
            {payload.rekor_entry_url && (
              <div className="sm:col-span-2">
                <dt className="text-muted-foreground text-xs uppercase tracking-wide">
                  {t("rekor")}
                </dt>
                <dd className="mt-0.5">
                  <a
                    href={payload.rekor_entry_url}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1 text-sm hover:underline"
                  >
                    {payload.rekor_log_index
                      ? t("rekorEntry", { index: payload.rekor_log_index })
                      : t("rekorVerify")}
                    <ExternalLinkIcon className="size-3" />
                  </a>
                </dd>
              </div>
            )}
          </dl>
        )}
      </CardContent>
    </Card>
  );
}

function SbomCard({ event, loading }: { event: AstroliftEvent | null; loading: boolean }) {
  const t = useTranslations("apps.security.sbom");
  const tSig = useTranslations("apps.security.signing");
  const payload = (event && asObject(event.payload)) as SbomPayload | null;
  const hasEvent = event !== null && payload !== null;

  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-3 space-y-0 pb-3">
        <div className="flex items-start gap-3">
          <FileBoxIcon className="text-muted-foreground mt-0.5 size-4" />
          <div>
            <CardTitle className="text-sm">{t("title")}</CardTitle>
            <CardDescription>{t("description")}</CardDescription>
          </div>
        </div>
        {hasEvent && payload.component_count != null ? (
          <Badge variant="secondary">{t("components", { count: payload.component_count })}</Badge>
        ) : (
          <Badge variant="outline">{tSig("noData")}</Badge>
        )}
      </CardHeader>
      <CardContent className="space-y-3">
        {loading ? (
          <Skeleton className="h-16 w-full" />
        ) : !hasEvent ? (
          <p className="text-muted-foreground text-sm">{t("noEvent")}</p>
        ) : (
          <div className="flex flex-wrap items-center justify-between gap-3">
            <dl className="grid flex-1 grid-cols-1 gap-x-8 gap-y-2 text-sm sm:grid-cols-2">
              <Field
                label={t("format")}
                mono
                value={payload.format || "—"}
              />
              <Field
                label={t("generatedAt")}
                value={formatTime(payload.generated_at ?? event?.occurredAt ?? null)}
              />
              <Field
                label={tSig("imageDigest")}
                mono
                value={
                  payload.image_digest ? (
                    <span className="break-all">{payload.image_digest}</span>
                  ) : (
                    "—"
                  )
                }
              />
            </dl>
            {payload.artifact_url && (
              <Button asChild size="sm" variant="outline">
                <a href={payload.artifact_url} target="_blank" rel="noreferrer">
                  <DownloadIcon className="size-3.5" />
                  {t("download")}
                </a>
              </Button>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function ScanCard({ event, loading }: { event: AstroliftEvent | null; loading: boolean }) {
  const t = useTranslations("apps.security.scan");
  const tSig = useTranslations("apps.security.signing");
  const payload = (event && asObject(event.payload)) as ScanPayload | null;
  const hasEvent = event !== null && payload !== null;
  const counts = payload?.counts ?? { critical: 0, high: 0, medium: 0, low: 0 };
  const findings = payload?.findings ?? [];

  const sortedFindings = React.useMemo(() => {
    const order: Record<ScanFinding["severity"], number> = {
      critical: 0,
      high: 1,
      medium: 2,
      low: 3,
    };
    return [...findings].sort(
      (a, b) => (order[a.severity] ?? 9) - (order[b.severity] ?? 9),
    );
  }, [findings]);

  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-3 space-y-0 pb-3">
        <div className="flex items-start gap-3">
          <ScanLineIcon className="text-muted-foreground mt-0.5 size-4" />
          <div>
            <CardTitle className="text-sm">{t("title")}</CardTitle>
            <CardDescription>{t("description")}</CardDescription>
          </div>
        </div>
        {hasEvent ? (
          <div className="flex flex-wrap gap-1.5">
            <SeverityCount label={t("critical")} value={counts.critical} tone="critical" />
            <SeverityCount label={t("high")} value={counts.high} tone="high" />
            <SeverityCount label={t("medium")} value={counts.medium} tone="medium" />
            <SeverityCount label={t("low")} value={counts.low} tone="low" />
          </div>
        ) : (
          <Badge variant="outline">{tSig("noData")}</Badge>
        )}
      </CardHeader>
      <CardContent className="space-y-3 p-0">
        {loading ? (
          <div className="space-y-2 p-6">
            <Skeleton className="h-12 w-full" />
            <Skeleton className="h-12 w-full" />
          </div>
        ) : !hasEvent ? (
          <p className="text-muted-foreground p-6 text-sm">{t("noEvent")}</p>
        ) : sortedFindings.length === 0 ? (
          <div className="flex items-center gap-2 p-6 text-sm">
            <CheckCircle2Icon className="size-4 text-emerald-700 dark:text-emerald-300" />
            <span className="text-muted-foreground">
              {t("noVulns", { at: formatTime(payload.scanned_at ?? event?.occurredAt ?? null) })}
            </span>
          </div>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t("columns.cve")}</TableHead>
                <TableHead>{t("columns.severity")}</TableHead>
                <TableHead>{t("columns.package")}</TableHead>
                <TableHead>{t("columns.fix")}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {sortedFindings.map((f) => (
                <TableRow key={`${f.cve_id}-${f.package_name}`}>
                  <TableCell>
                    <a
                      href={`https://nvd.nist.gov/vuln/detail/${f.cve_id}`}
                      target="_blank"
                      rel="noreferrer"
                      className="font-mono text-xs hover:underline"
                    >
                      {f.cve_id}
                    </a>
                  </TableCell>
                  <TableCell>
                    <Badge className={SEVERITY_TONE[f.severity]}>
                      {f.severity}
                    </Badge>
                  </TableCell>
                  <TableCell className="font-mono text-xs">
                    {f.package_name}
                    <span className="text-muted-foreground">
                      {" "}@ {f.package_version}
                    </span>
                  </TableCell>
                  <TableCell className="font-mono text-xs">
                    {f.fixed_in_version ? (
                      <span className="text-emerald-700 dark:text-emerald-300">
                        {t("upgrade", { version: f.fixed_in_version })}
                      </span>
                    ) : (
                      <span className="text-muted-foreground">{t("noFix")}</span>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}

function SeverityCount({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone: ScanFinding["severity"];
}) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-xs ${SEVERITY_TONE[tone]}`}
    >
      <strong>{value}</strong>
      <span className="opacity-75">{label}</span>
    </span>
  );
}

function PolicyCard({
  appSlug,
  initialPolicy,
}: {
  appSlug: string;
  initialPolicy: AstroliftSecurityPolicy | typeof DEFAULT_POLICY;
}) {
  const t = useTranslations("apps.security.policy");
  const [policy, setPolicy] = React.useState({
    blockOnCriticalCves: initialPolicy.blockOnCriticalCves,
    blockOnMissingSignature: initialPolicy.blockOnMissingSignature,
    blockOnHighCveThreshold: initialPolicy.blockOnHighCveThreshold ?? null,
  });
  const [dirty, setDirty] = React.useState(false);
  const [updatePolicy, { loading: saving }] = useMutation<{
    updateAstroliftSecurityPolicy: AstroliftRegisteredAppMutationResult;
  }>(UPDATE_SECURITY_POLICY);

  function update<K extends keyof typeof DEFAULT_POLICY>(
    key: K,
    value: (typeof DEFAULT_POLICY)[K],
  ) {
    setPolicy((p) => ({ ...p, [key]: value }));
    setDirty(true);
  }

  function reset() {
    setPolicy({
      blockOnCriticalCves: initialPolicy.blockOnCriticalCves,
      blockOnMissingSignature: initialPolicy.blockOnMissingSignature,
      blockOnHighCveThreshold: initialPolicy.blockOnHighCveThreshold ?? null,
    });
    setDirty(false);
  }

  async function handleSave() {
    const { data } = await updatePolicy({
      variables: {
        input: {
          appSlug,
          blockOnCriticalCves: policy.blockOnCriticalCves,
          blockOnMissingSignature: policy.blockOnMissingSignature,
          blockOnHighCveThreshold: policy.blockOnHighCveThreshold,
        },
      },
    });
    if (data?.updateAstroliftSecurityPolicy?.ok) {
      setDirty(false);
    }
  }

  return (
    <Card>
      <CardHeader className="flex flex-row items-start gap-3 space-y-0 pb-3">
        <ShieldOffIcon className="text-muted-foreground mt-0.5 size-4" />
        <div className="flex-1">
          <CardTitle className="text-sm">{t("title")}</CardTitle>
          <CardDescription>{t("description")}</CardDescription>
        </div>
      </CardHeader>
      <CardContent className="space-y-5">
        <ToggleRow
          label={t("blockCves")}
          description={t("blockCvesDesc")}
          checked={policy.blockOnCriticalCves}
          disabled={saving}
          onChange={(v) => update("blockOnCriticalCves", v)}
        />
        <ToggleRow
          label={t("blockSig")}
          description={t("blockSigDesc")}
          checked={policy.blockOnMissingSignature}
          disabled={saving}
          onChange={(v) => update("blockOnMissingSignature", v)}
        />

        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm font-medium">{t("highThreshold")}</p>
              <p className="text-muted-foreground text-xs">{t("highThresholdDesc")}</p>
            </div>
            <select
              className="border-input bg-background rounded-md border px-2 py-1 text-sm disabled:opacity-50"
              value={policy.blockOnHighCveThreshold ?? ""}
              disabled={saving}
              onChange={(e) =>
                update(
                  "blockOnHighCveThreshold",
                  e.target.value ? Number(e.target.value) : null,
                )
              }
            >
              <option value="">{t("noThreshold")}</option>
              <option value="1">≥ 1 high</option>
              <option value="3">≥ 3 high</option>
              <option value="5">≥ 5 high</option>
              <option value="10">≥ 10 high</option>
            </select>
          </div>
        </div>

        <Can permission="app.update">
          <div className="flex justify-end gap-2 pt-1">
            <Button variant="outline" disabled={!dirty || saving} onClick={reset}>
              {t("reset")}
            </Button>
            <Button disabled={!dirty || saving} onClick={handleSave}>
              {t("save")}
            </Button>
          </div>
        </Can>
      </CardContent>
    </Card>
  );
}

function ToggleRow({
  label,
  description,
  checked,
  disabled,
  onChange,
}: {
  label: string;
  description: string;
  checked: boolean;
  disabled: boolean;
  onChange: (next: boolean) => void;
}) {
  return (
    <label className="flex items-start gap-3 rounded-md border p-3">
      <input
        type="checkbox"
        className="mt-1 size-4"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
      />
      <div className="flex-1">
        <p className="text-sm font-medium">{label}</p>
        <p className="text-muted-foreground text-xs">{description}</p>
      </div>
    </label>
  );
}

function Field({
  label,
  value,
  mono,
}: {
  label: string;
  value: React.ReactNode;
  mono?: boolean;
}) {
  return (
    <div>
      <dt className="text-muted-foreground text-xs uppercase tracking-wide">
        {label}
      </dt>
      <dd className={mono ? "font-mono text-sm" : "text-sm"}>{value}</dd>
    </div>
  );
}
