"use client";

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
import type { Column } from "@/components/data-table";
import { EmptyState } from "@/components/EmptyState";
import { ListPage } from "@/components/list/ListPage";
import { type SelectRowsSpec, selectRows } from "@/components/list/select-rows";
import {
  type ListDefinition,
  standardViews,
  useLocalListState,
} from "@/components/list/use-list-state";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { AstroliftSecurityPolicy } from "@/graphql/__generated__/operations";

import {
  type AstroliftEvent,
  DEFAULT_POLICY,
  type SbomPayload,
  type ScanFinding,
  type ScanPayload,
  type SecurityPolicyDraft,
  type SigningPayload,
  type useAppSecurity,
} from "./use-app-security";

export type AppSecurityScreenProps = ReturnType<typeof useAppSecurity> & {
  /** The app detail tab row. */
  tabs?: React.ReactNode;
  /** The access card, which fetches its own rule. */
  access?: React.ReactNode;
};

const SEVERITY_TONE: Record<ScanFinding["severity"], string> = {
  critical: "bg-danger/25 text-danger-fg",
  high: "bg-danger/10 text-danger-fg",
  medium: "bg-warning/15 text-warning-fg",
  low: "bg-foreground/5 text-muted-foreground",
};

const SEVERITY_RANK: Record<ScanFinding["severity"], number> = {
  critical: 0,
  high: 1,
  medium: 2,
  low: 3,
};

const findingKey = (f: ScanFinding) => `${f.cve_id}-${f.package_name}`;

/**
 * The latest scan's findings as an embedded list: arrays inside one event
 * payload, so search, filters, sort and pages run in the client. Findings
 * are the image's, not a person's, so Mine is empty.
 */
const FINDINGS_SELECT: SelectRowsSpec<ScanFinding> = {
  filter: {
    owner: () => false,
    severity: (f, value) => f.severity === value,
    fix: (f, value) => (value === "available" ? Boolean(f.fixed_in_version) : !f.fixed_in_version),
  },
  text: (f) => [f.cve_id, f.package_name, f.package_version, f.fixed_in_version],
  sort: {
    severity: (f) => SEVERITY_RANK[f.severity] ?? 9,
    cve: (f) => f.cve_id,
    package: (f) => f.package_name.toLowerCase(),
  },
  id: findingKey,
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

/**
 * App security tab: who may enter, image signing, SBOM, vulnerability scan
 * and the supply-chain policy that gates deploys.
 */
export function AppSecurityScreen({
  slug,
  app: a,
  loading,
  eventsLoading,
  latestSigning,
  latestSbom,
  latestScan,
  savingPolicy,
  onSavePolicy,
  tabs,
  access,
}: AppSecurityScreenProps) {
  const tCommon = useTranslations("apps.common");
  const t = useTranslations("apps.security");

  if (loading && !a) {
    return (
      <PageShell title={t("loadingTitle")} description={tCommon("loading")}>
        <Skeleton className="h-32 w-full" />
      </PageShell>
    );
  }

  if (!a) {
    return (
      <PageShell title={tCommon("notFound")} description={tCommon("notFoundPermission")}>
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
      {tabs}

      {access}
      <SigningCard event={latestSigning} loading={eventsLoading} />
      <SbomCard event={latestSbom} loading={eventsLoading} />
      <ScanCard event={latestScan} loading={eventsLoading} />
      <PolicyCard
        initialPolicy={a.securityPolicy ?? DEFAULT_POLICY}
        saving={savingPolicy}
        onSave={onSavePolicy}
      />
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
          <ShieldCheckIcon className="text-success-fg mt-0.5 size-4" />
          <div>
            <CardTitle className="text-sm">{t("title")}</CardTitle>
            <CardDescription>{t("description")}</CardDescription>
          </div>
        </div>
        {hasEvent ? (
          <Badge className="bg-success/15 text-success-fg">{t("signed")}</Badge>
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
            <Field label={t("signerIdentity")} mono value={payload.signer_identity || "—"} />
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
                <dt className="text-muted-foreground text-xs tracking-wide uppercase">
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
              <Field label={t("format")} mono value={payload.format || "—"} />
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

  const list = useLocalListState(useFindingsList());
  const page = selectRows(
    findings,
    {
      filters: list.filters,
      q: list.state.q,
      sort: list.state.sort,
      page: list.state.page,
      pageSize: list.state.pageSize,
    },
    FINDINGS_SELECT
  );

  const columns: Column<ScanFinding>[] = [
    {
      id: "cve",
      header: t("columns.cve"),
      sortKey: "cve",
      cell: (f) => (
        <a
          href={`https://nvd.nist.gov/vuln/detail/${f.cve_id}`}
          target="_blank"
          rel="noreferrer"
          className="font-mono text-xs hover:underline"
        >
          {f.cve_id}
        </a>
      ),
    },
    {
      id: "severity",
      header: t("columns.severity"),
      sortKey: "severity",
      cell: (f) => <Badge className={SEVERITY_TONE[f.severity]}>{f.severity}</Badge>,
    },
    {
      id: "package",
      header: t("columns.package"),
      sortKey: "package",
      cellClassName: "font-mono text-xs",
      cell: (f) => (
        <>
          {f.package_name}
          <span className="text-muted-foreground"> @ {f.package_version}</span>
        </>
      ),
    },
    {
      id: "fix",
      header: t("columns.fix"),
      cellClassName: "font-mono text-xs",
      cell: (f) =>
        f.fixed_in_version ? (
          <span className="text-success-fg">{t("upgrade", { version: f.fixed_in_version })}</span>
        ) : (
          <span className="text-muted-foreground">{t("noFix")}</span>
        ),
    },
  ];

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
        ) : findings.length === 0 ? (
          <div className="flex items-center gap-2 p-6 text-sm">
            <CheckCircle2Icon className="text-success-fg size-4" />
            <span className="text-muted-foreground">
              {t("noVulns", { at: formatTime(payload.scanned_at ?? event?.occurredAt ?? null) })}
            </span>
          </div>
        ) : (
          <div className="px-6 pb-6">
            <ListPage<ScanFinding>
              embedded
              list={list}
              label="Findings"
              columns={columns}
              rows={page.rows}
              getRowId={findingKey}
              totalCount={page.totalCount}
              empty={{ icon: <ScanLineIcon className="size-5" />, title: t("title") }}
            />
          </div>
        )}
      </CardContent>
    </Card>
  );
}

/** The findings list's declaration; its labels are the scan card's own copy. */
function useFindingsList(): ListDefinition {
  const t = useTranslations("apps.security.scan");
  const [def] = React.useState<ListDefinition>(() => ({
    id: "apps.security.findings",
    fields: [
      {
        key: "severity",
        label: t("columns.severity"),
        options: (["critical", "high", "medium", "low"] as const).map((v) => ({
          value: v,
          label: t(v),
        })),
      },
      {
        key: "fix",
        label: t("columns.fix"),
        options: [
          { value: "available", label: t("columns.fix") },
          { value: "none", label: t("noFix") },
        ],
      },
    ],
    searchPlaceholder: "Search CVEs, packages…",
    defaultSort: [{ key: "severity", dir: "asc" }],
    views: standardViews({ owner: "me" }, [], {
      mineNote: "Findings are the image's, not a person's, so Mine is empty.",
    }),
    paging: "numbered",
    pageSizes: [25, 50, 100],
  }));
  return def;
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
  initialPolicy,
  saving,
  onSave,
}: {
  initialPolicy: AstroliftSecurityPolicy | SecurityPolicyDraft;
  saving: boolean;
  onSave: (policy: SecurityPolicyDraft) => Promise<boolean>;
}) {
  const t = useTranslations("apps.security.policy");
  const [policy, setPolicy] = React.useState({
    blockOnCriticalCves: initialPolicy.blockOnCriticalCves,
    blockOnMissingSignature: initialPolicy.blockOnMissingSignature,
    blockOnHighCveThreshold: initialPolicy.blockOnHighCveThreshold ?? null,
  });
  const [dirty, setDirty] = React.useState(false);

  function update<K extends keyof SecurityPolicyDraft>(key: K, value: SecurityPolicyDraft[K]) {
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
    if (await onSave(policy)) {
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
                update("blockOnHighCveThreshold", e.target.value ? Number(e.target.value) : null)
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

function Field({ label, value, mono }: { label: string; value: React.ReactNode; mono?: boolean }) {
  return (
    <div>
      <dt className="text-muted-foreground text-xs tracking-wide uppercase">{label}</dt>
      <dd className={mono ? "font-mono text-sm" : "text-sm"}>{value}</dd>
    </div>
  );
}
