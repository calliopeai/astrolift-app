"use client";

import {
  AlertTriangleIcon,
  BookOpenIcon,
  GlobeIcon,
  Loader2Icon,
  PauseIcon,
  PlayIcon,
  PlusIcon,
} from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import * as React from "react";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { AstroliftAppEnvironment } from "@/graphql/lifecycle/lifecycle.types";
import { DOC_LINKS } from "@/lib/docs/urls";

import { AddDomainSheet } from "./AddDomainSheet";
import { DomainHandshakeCard } from "./DomainHandshakeCard";
import { UploadCertSheet } from "./UploadCertSheet";
import type { AppDomain, AppDomainsState } from "./use-app-domains";

export type DomainsScreenProps = AppDomainsState & {
  slug: string;
  /** The app's tab bar, rendered under the page header. */
  tabs: React.ReactNode;
};

/**
 * The app's custom domains tab: env-scoped ingress controls, one handshake
 * card per domain, and the add / remove / BYO-cert flows. Data and
 * mutations come from useAppDomains; this holds only UI state (which
 * domain is being removed or given a certificate).
 */
export function DomainsScreen({
  slug,
  tabs,
  loading,
  domains: list,
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
  const [removeTarget, setRemoveTarget] = React.useState<AppDomain | null>(null);
  const [byoTarget, setByoTarget] = React.useState<AppDomain | null>(null);

  return (
    <PageShell
      title={t("title")}
      description={
        <span className="text-muted-foreground font-mono text-xs">
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
            <Button onClick={() => setAddOpen(true)}>
              <PlusIcon className="size-4" />
              {t("addDomain")}
            </Button>
          </Can>
        </>
      }
    >
      {tabs}

      <div className="space-y-4">
        {envList.length > 0 && (
          <div className="space-y-3">
            {envList.map((env) => (
              <IngressStatusCard
                key={env.id}
                env={env}
                domains={list}
                busy={busy}
                onToggle={() => toggleIngress(env)}
              />
            ))}
          </div>
        )}

        {loading && list.length === 0 ? (
          <Card>
            <CardContent className="space-y-2 p-6">
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
            </CardContent>
          </Card>
        ) : list.length === 0 ? (
          <Card>
            <CardContent className="p-6">
              <EmptyState
                icon={<GlobeIcon className="size-5" />}
                title={t("emptyTitle")}
                description={t("emptyDescription")}
              />
            </CardContent>
          </Card>
        ) : (
          <div className="space-y-4">
            {list.map((d) => (
              <DomainHandshakeCard
                key={d.id}
                domain={d}
                busy={busy}
                workloadOptions={workloadOptions}
                onRecheck={() => recheckDomain(d)}
                onRemove={() => setRemoveTarget(d)}
                onUploadCert={() => setByoTarget(d)}
                onSaveRedirects={(rules) => saveRedirects(d, rules)}
                onSavePathRoutes={(routes) => savePathRoutes(d, routes)}
              />
            ))}
          </div>
        )}
      </div>

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
// Env-scoped ingress control. Renders above the per-domain handshake
// list. Operator-facing pause / resume of all ingresses bound to the
// environment (managed hostname + custom domains alike). When paused
// the env's ingresses return HTTP 503 — used for planned maintenance
// windows where you want to freeze traffic without tearing the
// workload down. Per-domain delete still lives on the
// DomainHandshakeCard below; this card complements that with the
// env-wide toggle.

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
    <Card>
      <CardContent className="space-y-3 p-5">
        <div className="flex flex-wrap items-baseline gap-3">
          <StatusDot status={tone} />
          <span className="text-sm font-semibold capitalize">{env.name}</span>
          <span className="text-muted-foreground text-xs">{t("label")}</span>
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
              className="ml-auto"
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
        </div>

        <p className="text-muted-foreground text-xs">{t("description")}</p>

        <div className="border-border bg-muted/30 space-y-1.5 rounded-md border p-3 text-xs">
          <div className="text-muted-foreground">{t("bound")}</div>
          {managedHost && (
            <div className="flex items-center gap-2">
              <code className="font-mono break-all">{managedHost}</code>
              <Badge variant="outline" className="text-2xs">
                {tCert("managed")}
              </Badge>
            </div>
          )}
          {customHosts.length === 0
            ? !managedHost && <p className="text-muted-foreground italic">{t("noneBound")}</p>
            : customHosts.map((h) => (
                <div key={h} className="flex items-center gap-2">
                  <code className="font-mono break-all">{h}</code>
                  <Badge variant="outline" className="text-2xs">
                    {tCert("custom")}
                  </Badge>
                </div>
              ))}
        </div>

        {paused && (
          <div className="border-warning-border bg-warning/5 flex items-start gap-2 rounded-md border p-2 text-xs">
            <AlertTriangleIcon className="text-warning-fg size-3.5 shrink-0" />
            <span className="text-muted-foreground">
              <span className="text-warning-fg font-medium">{t("maintenance")}</span>{" "}
              {t("maintenanceDesc")}
            </span>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
