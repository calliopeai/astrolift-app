"use client";

import {
  AlertTriangleIcon,
  BookOpenIcon,
  GlobeIcon,
  Loader2Icon,
  PauseIcon,
  PlayIcon,
  PlusIcon,
  RefreshCwIcon,
  Trash2Icon,
} from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import * as React from "react";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import type { Column } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import { PageShell } from "@/components/PageShell";
import { Panel, PanelGrid } from "@/components/panel/Panel";
import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { AstroliftAppEnvironment } from "@/graphql/lifecycle/lifecycle.types";
import { DOC_LINKS } from "@/lib/docs/urls";

import { AddDomainSheet } from "./AddDomainSheet";
import {
  CERT_LABEL_KEYS,
  CERT_TONE,
  CertExpiryBadge,
  DomainHandshakeCard,
  EdgeAuthBadge,
} from "./DomainHandshakeCard";
import { UploadCertSheet } from "./UploadCertSheet";
import type { AppDomain, AppDomainsState } from "./use-app-domains";

export type DomainsScreenProps = AppDomainsState & {
  slug: string;
  /** The app's tab bar, rendered under the page header. */
  tabs: React.ReactNode;
};

/**
 * The domain the panel under the list shows: the one picked, else the first
 * still waiting on DNS (the handshake the operator came to finish), else the
 * first.
 */
export function pickShownDomain(list: AppDomain[], pickedId: string | null): AppDomain | null {
  return (
    list.find((d) => d.id === pickedId) ??
    list.find((d) => d.certState !== "validated") ??
    list[0] ??
    null
  );
}

/**
 * The app's Domains tab (spec 44 §5.1, §5.2): the domains on the embedded
 * list (the tab's one list: filtered, sorted and paged in the browser, see
 * domains-list.ts), the picked domain's handshake panel under it (DNS
 * records to add, certificate, redirects, path routes), then one ingress
 * panel per environment. A row links to `?domain=<id>`, which picks it. Add
 * domain and a certificate upload open sheets (§5.4). Data and mutations
 * come from useAppDomains; this holds only UI state (which domain is removed
 * or given a certificate).
 */
export function DomainsScreen({
  slug,
  tabs,
  loading,
  error,
  refetch,
  domains: all,
  list,
  rows,
  totalCount,
  pickedId,
  domainHref,
  environments: envList,
  workloadOptions,
  busy,
  addOpen,
  setAddOpen,
  addIsWildcard,
  setAddIsWildcard,
  clusterProviderSlug,
  showCertCombobox,
  certs,
  certsLoading,
  addDomain,
  uploadCertificate,
  removeDomain,
  recheckDomain,
  saveRedirects,
  savePathRoutes,
  toggleIngress,
}: DomainsScreenProps) {
  const t = useTranslations("apps.domains");
  const tCert = useTranslations("apps.domains.cert");
  const [removeTarget, setRemoveTarget] = React.useState<AppDomain | null>(null);
  const [byoTarget, setByoTarget] = React.useState<AppDomain | null>(null);
  const shown = pickShownDomain(all, pickedId);

  const columns: Column<AppDomain>[] = [
    {
      id: "hostname",
      header: t("columns.hostname"),
      sortKey: "hostname",
      cellClassName: "whitespace-normal",
      cell: (d) => (
        <span className="flex min-w-0 flex-wrap items-center gap-2">
          <code className="min-w-0 font-mono text-xs [overflow-wrap:anywhere]">
            {d.isWildcard ? `*.${d.hostname}` : d.hostname}
          </code>
          {d.isWildcard && (
            <Badge variant="outline" className="text-2xs">
              wildcard
            </Badge>
          )}
        </span>
      ),
    },
    {
      id: "status",
      header: t("columns.status"),
      sortKey: "status",
      width: "w-40",
      cell: (d) => {
        const key = CERT_LABEL_KEYS[d.certState];
        return (
          <span className="inline-flex items-center gap-2 text-xs">
            <StatusDot status={CERT_TONE[d.certState] ?? "pending"} />
            {key ? tCert(key) : d.certState}
          </span>
        );
      },
    },
    {
      id: "certificate",
      header: t("columns.certificate"),
      cellClassName: "whitespace-normal",
      cell: (d) => (
        <span className="flex flex-wrap items-center gap-1">
          <CertExpiryBadge
            expiresAt={d.certExpiresAt ?? null}
            status={d.certObservabilityStatus ?? ""}
          />
          <EdgeAuthBadge state={d.edgeAuthState} />
        </span>
      ),
    },
    {
      id: "checked",
      header: t("columns.lastChecked"),
      sortKey: "checked",
      width: "w-44",
      cellClassName: "text-muted-foreground font-mono text-xs",
      cell: (d) =>
        d.lastCheckedAt ? new Date(d.lastCheckedAt).toLocaleString() : tCert("notChecked"),
    },
    {
      id: "actions",
      header: <span className="sr-only">{t("columns.actions")}</span>,
      align: "right",
      width: "w-24",
      cell: (d) => (
        <Can permission="app.deploy">
          {/* Above the row's stretched activator, so these act, not select. */}
          <span className="relative z-10 inline-flex items-center gap-1">
            <Button
              size="icon"
              variant="ghost"
              className="size-8"
              onClick={() => recheckDomain(d)}
              disabled={busy}
              aria-label={`${tCert("recheck")} ${d.hostname}`}
              title={tCert("recheck")}
            >
              <RefreshCwIcon className="size-4" />
            </Button>
            <Button
              size="icon"
              variant="ghost"
              className="size-8"
              onClick={() => setRemoveTarget(d)}
              disabled={busy}
              aria-label={`${tCert("remove")} ${d.hostname}`}
              title={tCert("remove")}
            >
              <Trash2Icon className="size-4" />
            </Button>
          </span>
        </Can>
      ),
    },
  ];

  return (
    <PageShell
      title={t("title")}
      description={
        <span className="font-mono text-xs [overflow-wrap:anywhere]">
          {t("description", { slug })}
        </span>
      }
      actions={
        <>
          {/* Cross-link to the org-level DNS zones surface (#918). */}
          <Button asChild size="sm" variant="outline">
            <Link href="/domains">Org DNS zones</Link>
          </Button>
          <Button asChild size="sm" variant="outline">
            <Link href={DOC_LINKS.customDomains}>
              <BookOpenIcon className="size-4" />
              {t("learnMore")}
            </Link>
          </Button>
          <Can permission="app.deploy">
            <Button size="sm" onClick={() => setAddOpen(true)}>
              <PlusIcon className="size-4" />
              {t("addDomain")}
            </Button>
          </Can>
        </>
      }
    >
      {tabs}

      <PanelGrid>
        <div className="col-span-12 min-w-0">
          <ListPage<AppDomain>
            embedded
            list={list}
            label={t("listTitle")}
            columns={columns}
            rows={rows}
            getRowId={(d) => d.id}
            rowHref={domainHref}
            rowClassName={(d) => (d.id === shown?.id ? "bg-muted/50" : undefined)}
            loading={loading && all.length === 0}
            error={error && all.length === 0 ? error : null}
            onRetry={refetch}
            totalCount={totalCount}
            empty={{
              icon: <GlobeIcon className="size-5" />,
              title: t("emptyTitle"),
              description: t("emptyDescription"),
              learnMoreHref: DOC_LINKS.customDomains,
            }}
          />
        </div>

        {shown && (
          <DomainHandshakeCard
            key={shown.id}
            domain={shown}
            busy={busy}
            workloadOptions={workloadOptions}
            onRecheck={() => recheckDomain(shown)}
            onRemove={() => setRemoveTarget(shown)}
            onUploadCert={() => setByoTarget(shown)}
            onSaveRedirects={(rules) => saveRedirects(shown, rules)}
            onSavePathRoutes={(routes) => savePathRoutes(shown, routes)}
          />
        )}

        {envList.map((env) => (
          <IngressStatusCard
            key={env.id}
            env={env}
            domains={all}
            busy={busy}
            onToggle={() => toggleIngress(env)}
          />
        ))}
      </PanelGrid>

      <AddDomainSheet
        open={addOpen}
        onOpenChange={setAddOpen}
        onSubmit={addDomain}
        busy={busy}
        isWildcard={addIsWildcard}
        onWildcardChange={setAddIsWildcard}
        clusterProviderSlug={clusterProviderSlug}
        showCertCombobox={showCertCombobox}
        certs={certs}
        certsLoading={certsLoading}
      />

      <ConfirmDialog
        open={removeTarget !== null}
        onOpenChange={(next) => {
          if (!next) setRemoveTarget(null);
        }}
        title={
          removeTarget
            ? t("remove.title", { hostname: removeTarget.hostname })
            : t("remove.fallbackTitle")
        }
        description={t("remove.description")}
        confirmLabel={t("remove.confirm")}
        destructive
        onConfirm={async () => {
          if (removeTarget) await removeDomain(removeTarget);
        }}
      />

      <UploadCertSheet
        domain={byoTarget}
        open={byoTarget !== null}
        onOpenChange={(next) => {
          if (!next) setByoTarget(null);
        }}
        onSubmit={async (cert, key) => {
          if (!byoTarget) return false;
          const ok = await uploadCertificate(byoTarget, cert, key);
          if (ok) setByoTarget(null);
          return ok;
        }}
        busy={busy}
      />
    </PageShell>
  );
}

// ─── IngressStatusCard (#378) ───────────────────────────────────────────
// Env-scoped ingress control, one panel per environment under the domains:
// pause or resume every ingress bound to the environment (managed hostname
// and custom domains alike). Paused ingresses return HTTP 503, for planned
// maintenance that freezes traffic without tearing the workload down.
// Removing one domain stays on its row.

function IngressStatusCard({
  env,
  domains,
  busy,
  onToggle,
}: {
  env: AstroliftAppEnvironment;
  domains: AppDomain[];
  busy: boolean;
  onToggle: () => void;
}) {
  const t = useTranslations("apps.domains.ingress");
  const tCert = useTranslations("apps.domains.cert");
  const paused = env.ingressPaused;
  const tone = paused ? "warn" : "ok";
  const managedHost = (() => {
    try {
      return env.url ? new URL(env.url).host : "";
    } catch {
      return env.url;
    }
  })();
  const customHosts = domains.map((d) => d.hostname);

  return (
    <Panel
      span={6}
      title={`${env.name} ${t("label")}`}
      icon={<StatusDot status={tone} />}
      description={t("description")}
      actions={
        <>
          {paused ? (
            <Badge
              variant="outline"
              className="border-warning-border bg-warning/10 text-warning-fg"
            >
              <PauseIcon className="size-3" />
              {t("paused")}
            </Badge>
          ) : (
            <Badge
              variant="outline"
              className="border-success-border bg-success/10 text-success-fg"
            >
              <PlayIcon className="size-3" />
              {t("live")}
            </Badge>
          )}
          <Can permission="app.deploy">
            <Button
              size="sm"
              variant={paused ? "default" : "outline"}
              onClick={onToggle}
              disabled={busy}
            >
              {busy ? (
                <Loader2Icon className="size-3.5 animate-spin" />
              ) : paused ? (
                <PlayIcon className="size-3.5" />
              ) : (
                <PauseIcon className="size-3.5" />
              )}
              {paused ? t("resume") : t("pause")}
            </Button>
          </Can>
        </>
      }
    >
      {/* Maintenance is chosen, not failed: warning tone, never danger. */}
      {paused && (
        <div className="border-warning-border bg-warning/5 mb-3 flex min-w-0 items-start gap-2 rounded-md border p-2 text-xs">
          <AlertTriangleIcon className="text-warning-fg size-3.5 shrink-0" />
          <span className="text-muted-foreground min-w-0">
            <span className="text-warning-fg font-medium">{t("maintenance")}</span>{" "}
            {t("maintenanceDesc")}
          </span>
        </div>
      )}
      <div className="min-w-0 space-y-1.5 text-xs">
        <div className="text-muted-foreground">{t("bound")}</div>
        {managedHost && (
          <div className="flex min-w-0 items-center gap-2">
            <code className="min-w-0 font-mono [overflow-wrap:anywhere]">{managedHost}</code>
            <Badge variant="outline" className="text-2xs shrink-0">
              {tCert("managed")}
            </Badge>
          </div>
        )}
        {customHosts.length === 0
          ? !managedHost && <p className="text-muted-foreground italic">{t("noneBound")}</p>
          : customHosts.map((h) => (
              <div key={h} className="flex min-w-0 items-center gap-2">
                <code className="min-w-0 font-mono [overflow-wrap:anywhere]">{h}</code>
                <Badge variant="outline" className="text-2xs shrink-0">
                  {tCert("custom")}
                </Badge>
              </div>
            ))}
      </div>
    </Panel>
  );
}
