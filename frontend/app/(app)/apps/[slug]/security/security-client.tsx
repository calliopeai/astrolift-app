"use client";

import { useQuery } from "@apollo/client/react";
import {
  AlertTriangleIcon,
  CheckCircle2Icon,
  DownloadIcon,
  ExternalLinkIcon,
  FileBoxIcon,
  ScanLineIcon,
  ShieldCheckIcon,
  ShieldOffIcon,
} from "lucide-react";
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
import { GET_APP } from "@/graphql/registry/registry.queries";
import type { AstroliftRegisteredApp } from "@/graphql/registry/registry.types";

import { AppTabs } from "../components/app-tabs";

// Backend wiring tracked under #313. Until the security_policy
// fields + appSlug arg on astroliftEvents land, the toggles are
// disabled and the panels surface a 'backend wiring pending' banner
// with the contract the UI is reading against.
const BACKEND_READY = false;

// Default policy shown when the backend doesn't expose
// RegisteredApp.securityPolicy yet. Matches the strict-defaults
// proposal on #313 so the UI shows the safest stance up front.
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
      <PageShell title="Security" description="Loading…">
        <Skeleton className="h-32 w-full" />
      </PageShell>
    );
  }

  if (!a) {
    return (
      <PageShell
        title="App not found"
        description="The app doesn't exist or you don't have permission to view it."
      >
        <EmptyState
          icon={<ShieldCheckIcon className="size-5" />}
          title={`No app with slug ${slug}`}
          actionHref="/apps"
          actionLabel="Back to apps"
        />
      </PageShell>
    );
  }

  return (
    <PageShell
      title={`${a.name} · Security`}
      description="Supply-chain status for the latest build: image signing, vulnerability scan, SBOM, and the deploy-gating policy that decides when a build is safe to roll out."
    >
      <AppTabs slug={a.slug} active="security" />

      {!BACKEND_READY && (
        <Card className="border-amber-500/30 bg-amber-500/5">
          <CardHeader className="flex flex-row items-start gap-3 space-y-0 pb-3">
            <AlertTriangleIcon className="mt-0.5 size-4 text-amber-700 dark:text-amber-300" />
            <div className="flex-1">
              <CardTitle className="text-sm">Backend wiring pending</CardTitle>
              <CardDescription>
                The supply-chain workflows (image_signing, image_scan,
                sbom_multiarch) already emit events, but the per-app event
                filter and the <code className="bg-muted rounded px-1 font-mono">RegisteredApp.securityPolicy</code>{" "}
                fields aren&apos;t on main yet. The panels below read
                whatever org-wide events match this app and the policy
                toggles are disabled — tracked under{" "}
                <a
                  href="https://github.com/calliopeai/astrolift-app/issues/313"
                  target="_blank"
                  rel="noreferrer"
                  className="underline"
                >
                  #313
                </a>
                .
              </CardDescription>
            </div>
          </CardHeader>
        </Card>
      )}

      <SigningCard event={latestSigning} loading={events.loading && !events.data} />
      <SbomCard event={latestSbom} loading={events.loading && !events.data} />
      <ScanCard event={latestScan} loading={events.loading && !events.data} />
      <PolicyCard backendReady={BACKEND_READY} />
    </PageShell>
  );
}

function SigningCard({ event, loading }: { event: AstroliftEvent | null; loading: boolean }) {
  const payload = (event && asObject(event.payload)) as SigningPayload | null;
  const hasEvent = event !== null && payload !== null;

  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-3 space-y-0 pb-3">
        <div className="flex items-start gap-3">
          <ShieldCheckIcon className="mt-0.5 size-4 text-emerald-700 dark:text-emerald-300" />
          <div>
            <CardTitle className="text-sm">Image signing</CardTitle>
            <CardDescription>
              Cosign-keyless signature on the latest published image. Verifiable
              via the Sigstore transparency log.
            </CardDescription>
          </div>
        </div>
        {hasEvent ? (
          <Badge className="bg-emerald-500/15 text-emerald-700 dark:text-emerald-300">
            Signed
          </Badge>
        ) : (
          <Badge variant="outline">No data</Badge>
        )}
      </CardHeader>
      <CardContent className="space-y-3">
        {loading ? (
          <Skeleton className="h-16 w-full" />
        ) : !hasEvent ? (
          <p className="text-muted-foreground text-sm">
            No <code className="bg-muted rounded px-1 font-mono">image.signed</code>{" "}
            event for this app yet. The next CI run that publishes an image
            will emit one.
          </p>
        ) : (
          <dl className="grid grid-cols-1 gap-x-8 gap-y-2 text-sm sm:grid-cols-2">
            <Field label="Image tag" mono value={payload.image_tag || "—"} />
            <Field
              label="Signed at"
              value={formatTime(payload.signed_at ?? event?.occurredAt ?? null)}
            />
            <Field
              label="Signer identity"
              mono
              value={payload.signer_identity || "—"}
            />
            <Field
              label="Image digest"
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
                  Rekor transparency log
                </dt>
                <dd className="mt-0.5">
                  <a
                    href={payload.rekor_entry_url}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1 text-sm hover:underline"
                  >
                    {payload.rekor_log_index
                      ? `Entry #${payload.rekor_log_index}`
                      : "Verify entry"}
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
  const payload = (event && asObject(event.payload)) as SbomPayload | null;
  const hasEvent = event !== null && payload !== null;

  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-3 space-y-0 pb-3">
        <div className="flex items-start gap-3">
          <FileBoxIcon className="text-muted-foreground mt-0.5 size-4" />
          <div>
            <CardTitle className="text-sm">SBOM</CardTitle>
            <CardDescription>
              Software Bill of Materials for the latest image. Lists every
              package the runtime container includes.
            </CardDescription>
          </div>
        </div>
        {hasEvent && payload.component_count != null ? (
          <Badge variant="secondary">{payload.component_count} components</Badge>
        ) : (
          <Badge variant="outline">No data</Badge>
        )}
      </CardHeader>
      <CardContent className="space-y-3">
        {loading ? (
          <Skeleton className="h-16 w-full" />
        ) : !hasEvent ? (
          <p className="text-muted-foreground text-sm">
            No <code className="bg-muted rounded px-1 font-mono">sbom.generated</code>{" "}
            event for this app yet. The sbom_multiarch workflow runs on the
            next image push.
          </p>
        ) : (
          <div className="flex flex-wrap items-center justify-between gap-3">
            <dl className="grid flex-1 grid-cols-1 gap-x-8 gap-y-2 text-sm sm:grid-cols-2">
              <Field
                label="Format"
                mono
                value={payload.format || "—"}
              />
              <Field
                label="Generated at"
                value={formatTime(payload.generated_at ?? event?.occurredAt ?? null)}
              />
              <Field
                label="Image digest"
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
                  Download SBOM
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
            <CardTitle className="text-sm">Vulnerability scan</CardTitle>
            <CardDescription>
              Findings from the image_scan workflow on the latest published
              image. CVE list with suggested upgrade paths.
            </CardDescription>
          </div>
        </div>
        {hasEvent ? (
          <div className="flex flex-wrap gap-1.5">
            <SeverityCount label="Critical" value={counts.critical} tone="critical" />
            <SeverityCount label="High" value={counts.high} tone="high" />
            <SeverityCount label="Medium" value={counts.medium} tone="medium" />
            <SeverityCount label="Low" value={counts.low} tone="low" />
          </div>
        ) : (
          <Badge variant="outline">No data</Badge>
        )}
      </CardHeader>
      <CardContent className="space-y-3 p-0">
        {loading ? (
          <div className="space-y-2 p-6">
            <Skeleton className="h-12 w-full" />
            <Skeleton className="h-12 w-full" />
          </div>
        ) : !hasEvent ? (
          <p className="text-muted-foreground p-6 text-sm">
            No <code className="bg-muted rounded px-1 font-mono">image.scanned</code>{" "}
            event for this app yet. The image_scan workflow runs on the next
            image push.
          </p>
        ) : sortedFindings.length === 0 ? (
          <div className="flex items-center gap-2 p-6 text-sm">
            <CheckCircle2Icon className="size-4 text-emerald-700 dark:text-emerald-300" />
            <span className="text-muted-foreground">
              No vulnerabilities reported. Last scanned{" "}
              {formatTime(payload.scanned_at ?? event?.occurredAt ?? null)}.
            </span>
          </div>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>CVE</TableHead>
                <TableHead>Severity</TableHead>
                <TableHead>Package</TableHead>
                <TableHead>Fix available</TableHead>
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
                        upgrade → {f.fixed_in_version}
                      </span>
                    ) : (
                      <span className="text-muted-foreground">no fix yet</span>
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

function PolicyCard({ backendReady }: { backendReady: boolean }) {
  const [policy, setPolicy] = React.useState(DEFAULT_POLICY);
  const [dirty, setDirty] = React.useState(false);

  function update<K extends keyof typeof DEFAULT_POLICY>(
    key: K,
    value: (typeof DEFAULT_POLICY)[K],
  ) {
    setPolicy((p) => ({ ...p, [key]: value }));
    setDirty(true);
  }

  function reset() {
    setPolicy(DEFAULT_POLICY);
    setDirty(false);
  }

  return (
    <Card>
      <CardHeader className="flex flex-row items-start gap-3 space-y-0 pb-3">
        <ShieldOffIcon className="text-muted-foreground mt-0.5 size-4" />
        <div className="flex-1">
          <CardTitle className="text-sm">Deploy-gating policy</CardTitle>
          <CardDescription>
            What the promote-deploy workflow refuses to roll out. Rules
            evaluate at the moment of promotion against the latest scan + the
            signature on the image digest.
          </CardDescription>
        </div>
      </CardHeader>
      <CardContent className="space-y-5">
        <ToggleRow
          label="Block deploys on critical CVEs"
          description="Refuses to roll out an image whose latest scan reports any critical-severity vulnerability."
          checked={policy.blockOnCriticalCves}
          disabled={!backendReady}
          onChange={(v) => update("blockOnCriticalCves", v)}
        />
        <ToggleRow
          label="Block deploys on missing signature"
          description="Refuses to roll out an image that doesn't have a corresponding image.signed event. Sigstore-keyless via cosign on the CI side."
          checked={policy.blockOnMissingSignature}
          disabled={!backendReady}
          onChange={(v) => update("blockOnMissingSignature", v)}
        />

        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm font-medium">High-severity CVE threshold</p>
              <p className="text-muted-foreground text-xs">
                Set a count above which deploys get blocked. Leave empty to
                allow any number of high-severity findings (criticals still
                gated above).
              </p>
            </div>
            <select
              className="border-input bg-background rounded-md border px-2 py-1 text-sm disabled:opacity-50"
              value={policy.blockOnHighCveThreshold ?? ""}
              disabled={!backendReady}
              onChange={(e) =>
                update(
                  "blockOnHighCveThreshold",
                  e.target.value ? Number(e.target.value) : null,
                )
              }
            >
              <option value="">No threshold</option>
              <option value="1">≥ 1 high</option>
              <option value="3">≥ 3 high</option>
              <option value="5">≥ 5 high</option>
              <option value="10">≥ 10 high</option>
            </select>
          </div>
        </div>

        <Can permission="app.update">
          <div className="flex justify-end gap-2 pt-1">
            <Button variant="outline" disabled={!dirty || !backendReady} onClick={reset}>
              Reset
            </Button>
            <Button disabled={!dirty || !backendReady}>
              {backendReady ? "Save policy" : "Backend wiring pending"}
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
