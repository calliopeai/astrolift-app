"use client";

import {
  AlertTriangleIcon,
  CheckCircle2Icon,
  CopyIcon,
  GlobeIcon,
  Loader2Icon,
  PlusIcon,
  RefreshCwIcon,
  ShieldCheckIcon,
  Trash2Icon,
  UploadIcon,
} from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import { Can } from "@/components/Can";
import { Panel } from "@/components/panel/Panel";
import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

import type {
  AppDomain,
  DomainPathRoute,
  DomainRedirectRule,
  WorkloadOption,
} from "./use-app-domains";

export const CERT_TONE: Record<string, "ok" | "warn" | "error" | "pending"> = {
  validated: "ok",
  active: "ok",
  pending: "warn",
  validating: "pending",
  failed: "error",
};

export const CERT_LABEL_KEYS: Record<string, string> = {
  pending: "awaiting",
  validating: "validating",
  validated: "validated",
  failed: "failed",
};

/**
 * #731 — small inline badge surfacing the cert expiry window so an
 * operator scanning the domains list sees a stale cert before they
 * have to drill in. Renders nothing when the backend hasn't read the
 * cert yet (`expiresAt` null). When the observability worker reports
 * `failed` we show "Renewal failed" in destructive tone regardless of
 * the days-remaining count.
 */
export function CertExpiryBadge({
  expiresAt,
  status,
}: {
  expiresAt: string | null;
  status: string;
}) {
  const [now] = React.useState(() => Date.now());
  if (status === "failed") {
    return (
      <Badge variant="destructive" className="text-2xs">
        Renewal failed
      </Badge>
    );
  }
  if (!expiresAt) return null;
  const ms = new Date(expiresAt).getTime() - now;
  const days = Math.floor(ms / (1000 * 60 * 60 * 24));
  if (days < 0) {
    return (
      <Badge variant="destructive" className="text-2xs">
        Expired {Math.abs(days)}d ago
      </Badge>
    );
  }
  if (days <= 7) {
    return (
      <Badge variant="destructive" className="text-2xs">
        Expires in {days}d
      </Badge>
    );
  }
  if (days <= 14) {
    return (
      <Badge
        variant="outline"
        className="border-warning-border bg-warning/10 text-2xs text-warning-fg"
      >
        Expires in {days}d
      </Badge>
    );
  }
  return (
    <Badge variant="outline" className="text-muted-foreground text-2xs">
      Expires in {days}d
    </Badge>
  );
}

/**
 * #1621 — the app's managed subdomain is behind the cluster's login gate
 * and this custom domain is not, so the same backend is reachable here
 * without authenticating.
 *
 * Rendered only for ``ungated``. It is not a misconfiguration the
 * operator can toggle away: the gate's session cookie is scoped to the
 * platform's own zone and cannot be set for an external domain, so a
 * custom domain is outside it by construction. The badge exists because
 * the alternative was finding out by opening the URL.
 */
export function EdgeAuthBadge({ state }: { state?: string }) {
  const t = useTranslations("apps.domains.cert");
  if (state !== "ungated") return null;
  return (
    <Badge
      variant="outline"
      className="border-warning-border bg-warning/10 text-2xs text-warning-fg"
      title={t("edgeAuthUngatedHelp")}
    >
      {t("edgeAuthUngated")}
    </Badge>
  );
}

// ─── DomainHandshakeCard (#397) ──────────────────────────────────────────
// The picked domain's panel under the domains list (spec 44 §5.2). Until the
// domain validates it is the pending-verification panel: the DNS records to
// add, each in mono with a copy button, and their propagation. When the
// parent zone is platform-managed the records still render, with copy saying
// the operator has nothing to do. A validation error leads the panel.

export interface DomainHandshakeCardProps {
  domain: AppDomain;
  busy: boolean;
  workloadOptions: WorkloadOption[];
  onRecheck: () => void;
  onRemove: () => void;
  onUploadCert: () => void;
  onSaveRedirects: (rules: DomainRedirectRule[]) => Promise<boolean>;
  onSavePathRoutes: (routes: DomainPathRoute[]) => Promise<boolean>;
}

export function DomainHandshakeCard({
  domain,
  busy,
  workloadOptions,
  onRecheck,
  onRemove,
  onUploadCert,
  onSaveRedirects,
  onSavePathRoutes,
}: DomainHandshakeCardProps) {
  const t = useTranslations("apps.domains.cert");
  const tone = CERT_TONE[domain.certState] ?? "pending";
  const labelKey = CERT_LABEL_KEYS[domain.certState];
  const label = labelKey ? t(labelKey) : domain.certState;
  const records = domain.requiredDnsRecords ?? [];
  const allPropagated = records.length > 0 && records.every((r) => r.propagated);
  const pending = domain.certState !== "validated";
  const host = domain.isWildcard ? `*.${domain.hostname}` : domain.hostname;

  return (
    <Panel
      title={pending ? t("pendingTitle") : t("detailTitle")}
      icon={<GlobeIcon className="size-4" />}
      description={
        <span className="flex min-w-0 flex-wrap items-center gap-2">
          <StatusDot status={tone} />
          <code className="text-foreground font-mono text-sm [overflow-wrap:anywhere]">{host}</code>
          <Badge variant="secondary">{label}</Badge>
          {/* #682: wildcard, apart from the cert state, so a subtree reads at a glance. */}
          {domain.isWildcard && (
            <Badge variant="outline" className="text-2xs">
              wildcard
            </Badge>
          )}
          {domain.isPlatformManagedZone && (
            <Badge variant="outline" className="text-2xs">
              {t("platformZone")}
            </Badge>
          )}
          {/* #731: expiry, warning inside 14d, danger inside 7d. */}
          <CertExpiryBadge
            expiresAt={domain.certExpiresAt ?? null}
            status={domain.certObservabilityStatus ?? ""}
          />
          {/* #1621: gated on the managed subdomain, not here. */}
          <EdgeAuthBadge state={domain.edgeAuthState} />
          <span className="font-mono">
            {domain.lastCheckedAt
              ? t("lastChecked", { at: new Date(domain.lastCheckedAt).toLocaleString() })
              : t("notChecked")}
          </span>
        </span>
      }
      actions={
        <Can permission="app.deploy">
          <Button size="sm" variant="outline" onClick={onRecheck} disabled={busy}>
            <RefreshCwIcon className="size-3.5" />
            {t("recheck")}
          </Button>
          <Button
            variant="ghost"
            size="icon"
            className="size-8"
            onClick={onRemove}
            disabled={busy}
            aria-label={t("remove")}
            title={t("remove")}
          >
            <Trash2Icon className="size-4" />
          </Button>
        </Can>
      }
      failure={
        domain.lastValidationError
          ? { title: t("validationError"), reason: domain.lastValidationError }
          : null
      }
    >
      <div className="flex min-w-0 flex-col gap-4">
        {/* #682: the operator's SNI cert ref. Empty means platform-managed,
            which CertStateBlock already says, so the row is left out. */}
        {domain.sniCertRef && domain.sniCertRef.length > 0 && (
          <div className="text-muted-foreground flex min-w-0 items-baseline gap-2 text-xs">
            <span className="shrink-0">SNI cert ref:</span>
            <code className="text-foreground min-w-0 font-mono [overflow-wrap:anywhere]">
              {domain.sniCertRef}
            </code>
          </div>
        )}

        <CertStateBlock domain={domain} busy={busy} onUploadCert={onUploadCert} />

        {records.length > 0 ? (
          <div className="min-w-0 space-y-2">
            <div className="text-muted-foreground text-xs">
              {domain.isPlatformManagedZone
                ? t("platformManagedHelp")
                : domain.certState === "validated"
                  ? t("liveHelp")
                  : t("needsHelp")}
            </div>
            {/* The records to add: a handful per domain, each read and copied
                whole, so a row of labelled fields rather than a table. */}
            <ul className="border-border min-w-0 divide-y rounded-md border">
              {records.map((r, i) => (
                <li
                  key={`${r.kind}-${r.name}-${i}`}
                  className="flex min-w-0 items-start gap-3 px-3 py-2"
                >
                  <StatusDot status={r.propagated ? "ok" : "pending"} className="mt-1" />
                  <div className="min-w-0 flex-1 space-y-1.5">
                    <div className="flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-1 text-xs">
                      <span className="font-mono font-medium">{r.kind}</span>
                      <span className="text-muted-foreground font-mono">
                        {t("dnsColumns.ttl")} {r.ttl}s
                      </span>
                    </div>
                    <div className="grid min-w-0 gap-2 md:grid-cols-2">
                      <RecordField label={t("dnsColumns.name")}>
                        <CopyValue value={r.name} label={`${r.kind} name`} />
                      </RecordField>
                      <RecordField label={t("dnsColumns.value")}>
                        <CopyValue value={r.value} label={`${r.kind} value`} />
                      </RecordField>
                    </div>
                  </div>
                </li>
              ))}
            </ul>
            {allPropagated && domain.certState !== "validated" && (
              <p className="text-muted-foreground text-xs">{t("allPropagated")}</p>
            )}
          </div>
        ) : (
          <p className="text-muted-foreground text-sm">{t("noHandshake")}</p>
        )}

        <DomainRedirectsSection domain={domain} busy={busy} onSave={onSaveRedirects} />
        <DomainPathRoutesSection
          domain={domain}
          busy={busy}
          workloadOptions={workloadOptions}
          onSave={onSavePathRoutes}
        />
      </div>
    </Panel>
  );
}

/** A DNS record field in mono, wrapping anywhere, with its copy button. */
/** One labelled field of a record, rule or route row. */
function RecordField({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <p className="text-muted-foreground text-2xs">{label}</p>
      <div className="min-w-0">{children}</div>
    </div>
  );
}

function CopyValue({ value, label }: { value: string; label: string }) {
  return (
    <span className="flex min-w-0 items-start gap-1">
      <span className="min-w-0 flex-1 [overflow-wrap:anywhere]">{value}</span>
      <button
        type="button"
        onClick={() => {
          navigator.clipboard.writeText(value);
          toast.success(`${label} copied`);
        }}
        className="hover:bg-muted shrink-0 rounded p-1"
        aria-label={`Copy ${label}`}
        title={`Copy ${label}`}
      >
        <CopyIcon className="size-3.5" />
      </button>
    </span>
  );
}

// ─── DomainRedirectsSection (#685) ──────────────────────────────────────
// Replace-all UX: edits stay local to the section until the operator
// clicks "Save" — at that point the entire updated rules array is sent
// in one mutation and the backend swaps the persisted list. The
// per-row delete is a local mutation only until Save is pressed.

const REDIRECT_KINDS = [
  { value: "http_to_https", label: "HTTP → HTTPS" },
  { value: "apex_to_www", label: "Apex → www" },
  { value: "custom", label: "Custom" },
] as const;

type RedirectKind = (typeof REDIRECT_KINDS)[number]["value"];

function DomainRedirectsSection({
  domain,
  busy,
  onSave,
}: {
  domain: AppDomain;
  busy: boolean;
  onSave: (rules: DomainRedirectRule[]) => Promise<boolean>;
}) {
  const [rules, setRules] = React.useState<DomainRedirectRule[]>(domain.redirectRules ?? []);
  const [addOpen, setAddOpen] = React.useState(false);
  const [dirty, setDirty] = React.useState(false);

  // Sync local state when the upstream domain data changes (e.g. a
  // refetch after Save lands). Comparing by id-set is enough because
  // the backend re-emits the full list on every mutation.
  React.useEffect(() => {
    setRules(domain.redirectRules ?? []);
    setDirty(false);
  }, [domain.redirectRules]);

  const [draft, setDraft] = React.useState<{
    kind: RedirectKind;
    sourcePattern: string;
    destinationUrl: string;
    httpStatus: number;
    preserveQueryString: boolean;
  }>({
    kind: "http_to_https",
    sourcePattern: "",
    destinationUrl: "",
    httpStatus: 301,
    preserveQueryString: true,
  });

  function resetDraft() {
    setDraft({
      kind: "http_to_https",
      sourcePattern: "",
      destinationUrl: "",
      httpStatus: 301,
      preserveQueryString: true,
    });
  }

  function addRule() {
    const newRule: DomainRedirectRule = {
      id: `local-${Math.random().toString(36).slice(2, 10)}`,
      kind: draft.kind,
      sourcePattern: draft.kind === "custom" ? draft.sourcePattern : "",
      destinationUrl: draft.kind === "custom" ? draft.destinationUrl : "",
      httpStatus: draft.httpStatus,
      preserveQueryString: draft.preserveQueryString,
      priority: rules.length,
    };
    setRules([...rules, newRule]);
    setDirty(true);
    setAddOpen(false);
    resetDraft();
  }

  function deleteRule(id: string) {
    setRules(rules.filter((r) => r.id !== id));
    setDirty(true);
  }

  async function save() {
    const ok = await onSave(rules);
    if (ok) setDirty(false);
  }

  return (
    <section className="border-border space-y-2 rounded-md border p-3">
      <div className="flex items-center gap-2">
        <h4 className="text-sm font-semibold">Redirects</h4>
        <Badge variant="outline" className="text-2xs">
          {rules.length}
        </Badge>
        <div className="ml-auto flex items-center gap-2">
          {dirty && (
            <Can permission="app.deploy">
              <Button size="sm" onClick={save} disabled={busy}>
                Save
              </Button>
            </Can>
          )}
          <Can permission="app.deploy">
            <Button
              size="sm"
              variant="outline"
              onClick={() => setAddOpen((v) => !v)}
              disabled={busy}
            >
              <PlusIcon className="size-3.5" />
              Add redirect
            </Button>
          </Can>
        </div>
      </div>

      {rules.length > 0 ? (
        <ul className="border-border min-w-0 divide-y rounded-md border">
          {rules.map((r) => (
            <li key={r.id} className="flex min-w-0 items-start gap-3 px-3 py-2">
              <div className="min-w-0 flex-1 space-y-1.5">
                <div className="flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-1 text-xs">
                  <span className="font-mono font-medium">{r.kind}</span>
                  <span className="text-muted-foreground font-mono">{r.httpStatus}</span>
                  <span className="text-muted-foreground">
                    Query string {r.preserveQueryString ? "preserve" : "drop"}
                  </span>
                </div>
                <div className="grid min-w-0 gap-2 md:grid-cols-2">
                  <RecordField label="Source pattern">
                    <span className="font-mono text-xs [overflow-wrap:anywhere]">
                      {r.sourcePattern || "—"}
                    </span>
                  </RecordField>
                  <RecordField label="Destination">
                    <span className="font-mono text-xs [overflow-wrap:anywhere]">
                      {r.destinationUrl || "—"}
                    </span>
                  </RecordField>
                </div>
              </div>
              <Can permission="app.deploy">
                <button
                  type="button"
                  onClick={() => deleteRule(r.id)}
                  className="hover:bg-muted text-muted-foreground hover:text-destructive shrink-0 rounded p-1"
                  title="Remove rule"
                  aria-label="Remove rule"
                  disabled={busy}
                >
                  <Trash2Icon className="size-3.5" />
                </button>
              </Can>
            </li>
          ))}
        </ul>
      ) : (
        !addOpen && (
          <p className="text-muted-foreground text-xs">
            No redirects configured. Add one to send HTTP traffic to HTTPS or fold an apex into www.
          </p>
        )
      )}

      {addOpen && (
        <div className="bg-muted/30 border-border space-y-2 rounded-md border p-3">
          <div className="grid gap-2 sm:grid-cols-2">
            <div className="space-y-1">
              <Label className="text-xs">Kind</Label>
              <Select
                value={draft.kind}
                onValueChange={(v) => setDraft((d) => ({ ...d, kind: v as RedirectKind }))}
              >
                <SelectTrigger className="h-8">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {REDIRECT_KINDS.map((k) => (
                    <SelectItem key={k.value} value={k.value}>
                      {k.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1">
              <Label className="text-xs">Status code</Label>
              <Select
                value={String(draft.httpStatus)}
                onValueChange={(v) => setDraft((d) => ({ ...d, httpStatus: Number(v) }))}
              >
                <SelectTrigger className="h-8">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="301">301 (permanent)</SelectItem>
                  <SelectItem value="302">302 (temporary)</SelectItem>
                </SelectContent>
              </Select>
            </div>
            {draft.kind === "custom" && (
              <>
                <div className="space-y-1">
                  <Label className="text-xs">Source pattern</Label>
                  <Input
                    value={draft.sourcePattern}
                    onChange={(e) => setDraft((d) => ({ ...d, sourcePattern: e.target.value }))}
                    placeholder="/old-path"
                    className="h-8 font-mono text-xs"
                  />
                </div>
                <div className="space-y-1">
                  <Label className="text-xs">Destination URL</Label>
                  <Input
                    value={draft.destinationUrl}
                    onChange={(e) => setDraft((d) => ({ ...d, destinationUrl: e.target.value }))}
                    placeholder="https://example.com/new"
                    className="h-8 font-mono text-xs"
                  />
                </div>
              </>
            )}
          </div>
          <label className="text-muted-foreground inline-flex items-center gap-2 text-xs">
            <input
              type="checkbox"
              checked={draft.preserveQueryString}
              onChange={(e) => setDraft((d) => ({ ...d, preserveQueryString: e.target.checked }))}
            />
            Preserve query string
          </label>
          <div className="flex justify-end gap-2">
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                setAddOpen(false);
                resetDraft();
              }}
            >
              Cancel
            </Button>
            <Button
              size="sm"
              onClick={addRule}
              disabled={
                draft.kind === "custom" &&
                (!draft.sourcePattern.trim() || !draft.destinationUrl.trim())
              }
            >
              Add to list
            </Button>
          </div>
          <p className="text-muted-foreground text-2xs">
            New rules apply after you click Save above.
          </p>
        </div>
      )}
    </section>
  );
}

// ─── DomainPathRoutesSection (#686) ─────────────────────────────────────
// Same replace-all UX as redirects. Workload dropdown is populated from
// the app's registered workloads so we don't accept arbitrary slugs that
// the manifest renderer will reject downstream.

function DomainPathRoutesSection({
  domain,
  busy,
  workloadOptions,
  onSave,
}: {
  domain: AppDomain;
  busy: boolean;
  workloadOptions: WorkloadOption[];
  onSave: (routes: DomainPathRoute[]) => Promise<boolean>;
}) {
  const [routes, setRoutes] = React.useState<DomainPathRoute[]>(domain.pathRoutes ?? []);
  const [addOpen, setAddOpen] = React.useState(false);
  const [dirty, setDirty] = React.useState(false);

  React.useEffect(() => {
    setRoutes(domain.pathRoutes ?? []);
    setDirty(false);
  }, [domain.pathRoutes]);

  const [draft, setDraft] = React.useState<{
    pathPrefix: string;
    targetWorkloadSlug: string;
    targetPort: number;
    stripPrefix: boolean;
    priority: number;
  }>({
    pathPrefix: "",
    targetWorkloadSlug: "",
    targetPort: 80,
    stripPrefix: false,
    priority: 0,
  });

  function resetDraft() {
    setDraft({
      pathPrefix: "",
      targetWorkloadSlug: "",
      targetPort: 80,
      stripPrefix: false,
      priority: 0,
    });
  }

  function addRoute() {
    const newRoute: DomainPathRoute = {
      id: `local-${Math.random().toString(36).slice(2, 10)}`,
      pathPrefix: draft.pathPrefix,
      targetWorkloadSlug: draft.targetWorkloadSlug,
      targetPort: draft.targetPort,
      stripPrefix: draft.stripPrefix,
      priority: draft.priority,
    };
    setRoutes([...routes, newRoute]);
    setDirty(true);
    setAddOpen(false);
    resetDraft();
  }

  function deleteRoute(id: string) {
    setRoutes(routes.filter((r) => r.id !== id));
    setDirty(true);
  }

  async function save() {
    const ok = await onSave(routes);
    if (ok) setDirty(false);
  }

  const canAdd = draft.pathPrefix.trim() !== "" && draft.targetWorkloadSlug !== "";

  return (
    <section className="border-border space-y-2 rounded-md border p-3">
      <div className="flex items-center gap-2">
        <h4 className="text-sm font-semibold">Path routing</h4>
        <Badge variant="outline" className="text-2xs">
          {routes.length}
        </Badge>
        <div className="ml-auto flex items-center gap-2">
          {dirty && (
            <Can permission="app.deploy">
              <Button size="sm" onClick={save} disabled={busy}>
                Save
              </Button>
            </Can>
          )}
          <Can permission="app.deploy">
            <Button
              size="sm"
              variant="outline"
              onClick={() => setAddOpen((v) => !v)}
              disabled={busy}
            >
              <PlusIcon className="size-3.5" />
              Add route
            </Button>
          </Can>
        </div>
      </div>

      {routes.length > 0 ? (
        <ul className="border-border min-w-0 divide-y rounded-md border">
          {routes.map((r) => (
            <li key={r.id} className="flex min-w-0 items-start gap-3 px-3 py-2">
              <div className="min-w-0 flex-1 space-y-1">
                <p className="font-mono text-xs font-medium [overflow-wrap:anywhere]">
                  {r.pathPrefix}
                </p>
                <p className="text-muted-foreground flex min-w-0 flex-wrap gap-x-3 gap-y-1 text-xs">
                  <span className="min-w-0 [overflow-wrap:anywhere]">
                    Workload{" "}
                    <span className="text-foreground font-mono">{r.targetWorkloadSlug}</span>
                  </span>
                  <span>
                    Port <span className="text-foreground font-mono">{r.targetPort}</span>
                  </span>
                  <span>Strip {r.stripPrefix ? "yes" : "no"}</span>
                  <span>
                    Priority <span className="text-foreground font-mono">{r.priority}</span>
                  </span>
                </p>
              </div>
              <Can permission="app.deploy">
                <button
                  type="button"
                  onClick={() => deleteRoute(r.id)}
                  className="hover:bg-muted text-muted-foreground hover:text-destructive shrink-0 rounded p-1"
                  title="Remove route"
                  aria-label="Remove route"
                  disabled={busy}
                >
                  <Trash2Icon className="size-3.5" />
                </button>
              </Can>
            </li>
          ))}
        </ul>
      ) : (
        !addOpen && (
          <p className="text-muted-foreground text-xs">
            All traffic falls through to the default workload. Add a path prefix to send /api or
            /static elsewhere.
          </p>
        )
      )}

      {addOpen && (
        <div className="bg-muted/30 border-border space-y-2 rounded-md border p-3">
          <div className="grid gap-2 sm:grid-cols-2">
            <div className="space-y-1">
              <Label className="text-xs">Path prefix</Label>
              <Input
                value={draft.pathPrefix}
                onChange={(e) => setDraft((d) => ({ ...d, pathPrefix: e.target.value }))}
                placeholder="/api"
                className="h-8 font-mono text-xs"
              />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">Workload</Label>
              <Select
                value={draft.targetWorkloadSlug}
                onValueChange={(v) => setDraft((d) => ({ ...d, targetWorkloadSlug: v }))}
              >
                <SelectTrigger className="h-8">
                  <SelectValue placeholder="Select workload" />
                </SelectTrigger>
                <SelectContent>
                  {workloadOptions.length === 0 ? (
                    <SelectItem value="__empty" disabled>
                      No workloads registered
                    </SelectItem>
                  ) : (
                    workloadOptions.map((w) => (
                      <SelectItem key={w.slug} value={w.slug} className="font-mono text-xs">
                        {w.slug}
                      </SelectItem>
                    ))
                  )}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1">
              <Label className="text-xs">Target port</Label>
              <Input
                type="number"
                value={draft.targetPort}
                onChange={(e) =>
                  setDraft((d) => ({ ...d, targetPort: Number(e.target.value) || 0 }))
                }
                min={1}
                max={65535}
                className="h-8 font-mono text-xs"
              />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">Priority</Label>
              <Input
                type="number"
                value={draft.priority}
                onChange={(e) => setDraft((d) => ({ ...d, priority: Number(e.target.value) || 0 }))}
                className="h-8 font-mono text-xs"
              />
            </div>
          </div>
          <label className="text-muted-foreground inline-flex items-center gap-2 text-xs">
            <input
              type="checkbox"
              checked={draft.stripPrefix}
              onChange={(e) => setDraft((d) => ({ ...d, stripPrefix: e.target.checked }))}
            />
            Strip prefix before forwarding to workload
          </label>
          <div className="flex justify-end gap-2">
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                setAddOpen(false);
                resetDraft();
              }}
            >
              Cancel
            </Button>
            <Button size="sm" onClick={addRoute} disabled={!canAdd}>
              Add to list
            </Button>
          </div>
          <p className="text-muted-foreground text-2xs">
            New routes apply after you click Save above.
          </p>
        </div>
      )}
    </section>
  );
}

// ─── CertStateBlock (#397 unhappy path) ─────────────────────────────────
// Surfaces the certificate lifecycle as a separate axis from DNS
// validation. DNS can be validated while the cert is still issuing —
// rendering both axes lets the operator see exactly which step is in
// flight (and gives them an actionable BYO-cert escape hatch when
// auto-issuance fails for reasons the platform can't recover from:
// ACM-on-external-zone, LE rate limits, custom CAs, etc).

function CertStateBlock({
  domain,
  busy,
  onUploadCert,
}: {
  domain: AppDomain;
  busy: boolean;
  onUploadCert: () => void;
}) {
  const t = useTranslations("apps.domains.cert");
  const state = domain.certificateState || "not_requested";
  // not_requested with un-validated DNS: don't render — the DNS card
  // already tells the story. Once DNS validates, the worker fires
  // cert issuance and this block flips to "issuing".
  if (state === "not_requested" && domain.certState !== "validated") {
    return null;
  }
  if (state === "issuing") {
    return (
      <div className="text-muted-foreground border-warning-border bg-warning/5 flex items-center gap-2 rounded-md border p-2 text-xs">
        <Loader2Icon className="text-warning-fg size-3.5 animate-spin" />
        <span className="text-warning-fg font-medium">{t("issuing")}</span>
        <span>{domain.isPlatformManagedZone ? t("issuingPlatform") : t("issuingExternal")}</span>
      </div>
    );
  }
  if (state === "active") {
    return (
      <div className="border-success-border bg-success/5 flex items-center gap-2 rounded-md border p-2 text-xs">
        <ShieldCheckIcon className="text-success-fg size-3.5" />
        <span className="text-success-fg font-medium">{t("active")}</span>
        <span className="text-muted-foreground">
          {t("activeDesc", { hostname: domain.hostname })}
        </span>
      </div>
    );
  }
  if (state === "byo") {
    return (
      <div className="border-info-border bg-info/5 flex items-start gap-2 rounded-md border p-2 text-xs">
        <CheckCircle2Icon className="text-info-fg size-3.5 shrink-0" />
        <div className="flex-1 space-y-1">
          <div>
            <span className="text-info-fg font-medium">{t("byo")}</span>
            {domain.byoCertificateUploadedAt && (
              <span className="text-muted-foreground ml-2">
                {t("byoUploaded", {
                  at: new Date(domain.byoCertificateUploadedAt).toLocaleString(),
                })}
              </span>
            )}
          </div>
          <p className="text-muted-foreground">{t("byoNote")}</p>
        </div>
        <Can permission="app.deploy">
          <Button size="sm" variant="ghost" onClick={onUploadCert} disabled={busy}>
            <UploadIcon className="size-3.5" />
            {t("replace")}
          </Button>
        </Can>
      </div>
    );
  }
  if (state === "failed") {
    return (
      <div className="border-destructive/30 bg-destructive/5 flex items-start gap-2 rounded-md border p-2 text-xs">
        <AlertTriangleIcon className="text-destructive size-3.5 shrink-0" />
        <div className="flex-1 space-y-1">
          <div className="text-destructive font-medium">{t("failedTitle")}</div>
          {domain.lastCertificateError && (
            <p className="text-muted-foreground font-mono break-all">
              {domain.lastCertificateError}
            </p>
          )}
          <p className="text-muted-foreground">{t("failedHelp")}</p>
        </div>
        <Can permission="app.deploy">
          <Button size="sm" variant="default" onClick={onUploadCert} disabled={busy}>
            <UploadIcon className="size-3.5" />
            {t("uploadCert")}
          </Button>
        </Can>
      </div>
    );
  }
  return (
    <div className="text-muted-foreground border-warning-border bg-warning/5 rounded-md border p-2 text-xs">
      {t("queued")}
    </div>
  );
}
