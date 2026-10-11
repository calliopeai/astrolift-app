"use client";

import Link from "next/link";
import { useTranslations } from "next-intl";
import * as React from "react";

import { ConfirmDialog } from "@/components/ConfirmDialog";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type {
  ManagedDomainProbeTool,
  ManagedDomainRecordType,
} from "@/graphql/__generated__/operations";
import { useFormatters } from "@/lib/i18n/formatters";
import { domainDiagnosticReasonKey } from "./domain-diagnostic-reasons";
import type { ManagedDomainState } from "./use-managed-domain";

export type ManagedDomainDetailProps = ManagedDomainState & {
  id: string;
  initialTab?: Tab;
  /**
   * Why Astrolift writes no DNS on this install (DNS withheld,
   * calliope-installer#447). Verify and Revalidate still run: the certificate
   * is requested and its validation records are left for the operator to
   * publish, so the page says why instead of disabling them.
   */
  dnsRestriction?: string | null;
};
type Tab = "overview" | "records" | "routing" | "diagnostics";
const recordTypes = [
  "A",
  "AAAA",
  "CNAME",
  "NS",
  "SOA",
  "TXT",
  "MX",
  "CAA",
  "SRV",
] as const satisfies readonly ManagedDomainRecordType[];
const tools = [
  "LOOKUP",
  "DIG",
  "PING",
  "TRACEROUTE",
  "HTTPS",
] as const satisfies readonly ManagedDomainProbeTool[];
const tabs: Tab[] = ["overview", "records", "routing", "diagnostics"];
const stateKeys = {
  OK: "ok",
  MISMATCH: "mismatch",
  UNKNOWN: "unknown",
  ERROR: "unavailable",
  UNSUPPORTED: "unsupported",
} as const;
const toolKeys = {
  LOOKUP: "lookup",
  DIG: "dig",
  PING: "ping",
  TRACEROUTE: "traceroute",
  HTTPS: "https",
} as const;

function DetailTabs({
  domain: d,
  initialTab = "overview",
  ...props
}: ManagedDomainDetailProps & { domain: NonNullable<ManagedDomainState["domain"]> }) {
  const t = useTranslations("managedDomains");
  const connectionText = useTranslations("domainConnections");
  const fmt = useFormatters();
  const tabId = React.useId();
  const [tab, setTab] = React.useState<Tab>(initialTab);
  const [search, setSearch] = React.useState("");
  const [filter, setFilter] = React.useState("all");
  const [hostname, setHostname] = React.useState(d.zone);
  const [tool, setTool] = React.useState<ManagedDomainProbeTool>("LOOKUP");
  const [recordType, setRecordType] = React.useState<ManagedDomainRecordType>("A");
  const [deleteOpen, setDeleteOpen] = React.useState(false);
  const [copyStatus, setCopyStatus] = React.useState<string | null>(null);
  const buttons = React.useRef<(HTMLButtonElement | null)[]>([]);
  const observation = props.diagnostics;
  const records = (observation?.providerZone.records ?? []).filter(
    (r) =>
      (filter === "all" || r.type === filter) &&
      [r.name, ...r.values, r.aliasTarget ?? ""]
        .join(" ")
        .toLowerCase()
        .includes(search.toLowerCase())
  );
  const busy =
    props.loading ||
    props.diagnosticsLoading ||
    props.revalidating ||
    props.verifying ||
    props.deleting ||
    props.removed;
  // Provisioning writes through the DNS cluster's driver whatever the row's
  // dns_driver says; only a read-only Cloudflare zone never reaches it.
  const dnsWithheld =
    props.dnsRestriction && d.dnsDriver !== "cloudflare_read_only" ? props.dnsRestriction : null;
  const checkName = (key: string) =>
    ({
      delegation: t("publicDelegation"),
      internal_dns: t("internalDns"),
      lookup: t("lookup"),
      effective_tenant_apps_domain: t("effectiveTenant"),
      effective_preview_domain: t("effectivePreview"),
    })[key] ?? key;
  const state = (value: keyof typeof stateKeys, reason?: string) => (
    <Badge variant={value === "MISMATCH" || value === "ERROR" ? "destructive" : "outline"}>
      {reason === "DNS_NO_DATA" && value === "OK"
        ? t("dnsNoDataStatus")
        : reason === "DNS_ANSWER" && value === "OK"
          ? t("answerObserved")
          : t(stateKeys[value])}
    </Badge>
  );
  async function copy(value: string) {
    try {
      await navigator.clipboard.writeText(value);
      setCopyStatus(t("copied"));
    } catch {
      setCopyStatus(t("copyFailed"));
    }
  }
  function values(rows: string[]) {
    return rows.length ? (
      <ul className="space-y-1">
        {rows.map((value, i) => (
          <li key={`${value}:${i}`} className="flex items-start gap-2">
            <code className="min-w-0 text-xs break-all">{value}</code>
            <Button
              size="xs"
              variant="ghost"
              aria-label={`${t("copy")} ${value}`}
              onClick={() => void copy(value)}
            >
              {t("copy")}
            </Button>
          </li>
        ))}
      </ul>
    ) : (
      <span>{t("unknown")}</span>
    );
  }
  function meta(perspective: string, checkedAt: string) {
    return (
      <dl className="grid gap-2 text-xs sm:grid-cols-2">
        <div>
          <dt className="text-muted-foreground">{t("perspective")}</dt>
          <dd className="break-all">{perspective}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">{t("checkedAt")}</dt>
          <dd>
            <time dateTime={checkedAt} title={checkedAt}>
              {fmt.formatDateTime(checkedAt)}
            </time>
          </dd>
        </div>
      </dl>
    );
  }
  function changeProbe(change: () => void) {
    props.onResetProbe();
    change();
  }
  function reason(value: string) {
    const key = domainDiagnosticReasonKey(value);
    return (
      <>
        <p className="text-sm">{key ? t(key) : value}</p>
        {key && (
          <p className="text-muted-foreground text-xs">
            {t("reason")}: <code>{value}</code>
          </p>
        )}
      </>
    );
  }
  const checks = (
    <div className="grid gap-3 lg:grid-cols-2">
      {observation?.checks.map((check) => (
        <section
          key={`${check.key}:${check.perspective}`}
          className="min-w-0 space-y-3 rounded-lg border p-4"
          aria-label={`${checkName(check.key)} · ${check.perspective}`}
          aria-busy={props.diagnosticsLoading}
        >
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h3 className="font-medium">{checkName(check.key)}</h3>
            {state(check.state, check.reason)}
          </div>
          {meta(check.perspective, check.checkedAt)}
          {reason(check.reason)}
          {check.key === "delegation" && (
            <p className="text-muted-foreground text-sm">{t("delegationScopeHelp")}</p>
          )}
          {check.state === "MISMATCH" && (
            <p className="text-sm font-medium">
              {check.key === "delegation"
                ? t("mismatchHelp")
                : check.key.startsWith("effective_")
                  ? t("defaultHelp")
                  : check.reason}
            </p>
          )}
          {check.state === "UNKNOWN" && (
            <p className="text-muted-foreground text-sm">{t("unknownHelp")}</p>
          )}
          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <h4 className="mb-1 text-xs font-semibold">{t("expected")}</h4>
              {values(check.expected)}
            </div>
            <div>
              <h4 className="mb-1 text-xs font-semibold">{t("observed")}</h4>
              {values(check.observed)}
            </div>
          </div>
          <Button variant="outline" size="sm" disabled={busy} onClick={props.onRefreshDiagnostics}>
            {t("refreshCheck")}
          </Button>
        </section>
      ))}
    </div>
  );
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2">
        {d.organizationSlug && (
          <Button asChild variant="outline">
            <Link href={`/domains/connect?domainId=${encodeURIComponent(d.id)}`}>
              {connectionText("title")}
            </Link>
          </Button>
        )}
        {d.verificationState === "pending" && (
          <Button
            variant="outline"
            disabled={busy || !props.canVerify}
            onClick={() => void props.onVerify()}
          >
            {connectionText("verify")}
          </Button>
        )}
        <Button
          variant="outline"
          disabled={busy || !observation?.actions.canRevalidate || !observation.provisionClusterId}
          onClick={() => void props.onRevalidate()}
        >
          {t("revalidate")}
        </Button>
        <Button
          variant="destructive"
          disabled={busy || !observation?.actions.canDelete}
          onClick={() => setDeleteOpen(true)}
        >
          {t("delete")}
        </Button>
      </div>
      {dnsWithheld && <p className="text-warning-fg text-sm">{dnsWithheld}</p>}
      {props.actionMessage && <p role="status">{props.actionMessage}</p>}
      {props.actionError && <p role="alert">{props.actionError}</p>}
      <ConfirmDialog
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        title={t("deleteTitle")}
        description={t("deleteHelp")}
        confirmLabel={t("delete")}
        destructive
        confirmDisabled={busy || !observation?.actions.canDelete}
        onConfirm={props.onDelete}
      />
      <div role="tablist" aria-label={t("title")} className="flex flex-wrap gap-1 border-b pb-2">
        {tabs.map((value, index) => (
          <Button
            key={value}
            ref={(node) => {
              buttons.current[index] = node;
            }}
            role="tab"
            id={`${tabId}-${value}`}
            aria-controls={`${tabId}-panel`}
            aria-selected={tab === value}
            tabIndex={tab === value ? 0 : -1}
            variant={tab === value ? "secondary" : "ghost"}
            onClick={() => setTab(value)}
            onKeyDown={(event) => {
              const next =
                event.key === "ArrowRight"
                  ? (index + 1) % tabs.length
                  : event.key === "ArrowLeft"
                    ? (index + tabs.length - 1) % tabs.length
                    : event.key === "Home"
                      ? 0
                      : event.key === "End"
                        ? tabs.length - 1
                        : null;
              if (next !== null) {
                event.preventDefault();
                setTab(tabs[next]);
                buttons.current[next]?.focus();
              }
            }}
          >
            {t(value)}
          </Button>
        ))}
      </div>
      {copyStatus && (
        <p role="status" className="text-sm">
          {copyStatus}
        </p>
      )}
      {props.diagnosticsError && (
        <div role="alert" className="space-y-2 rounded-lg border p-3">
          <p>{props.diagnosticsError}</p>
          <Button onClick={props.onRetry} disabled={props.loading}>
            {t("refresh")}
          </Button>
        </div>
      )}
      {props.diagnosticsLoading && <p role="status">{t("running")}</p>}
      <div
        role="tabpanel"
        id={`${tabId}-panel`}
        aria-labelledby={`${tabId}-${tab}`}
        className="min-w-0 space-y-4"
      >
        {tab === "overview" && (
          <>
            <section className="space-y-3 rounded-lg border p-4">
              <h2 className="font-semibold">{t("configuration")}</h2>
              <div className="flex flex-wrap gap-2">
                <Badge variant="outline">
                  {d.provisionState === "mark_active"
                    ? t("provisioned")
                    : d.provisionState
                      ? t("provisioning")
                      : t("unprovisioned")}
                </Badge>
                <Badge variant="outline">{d.verificationState || t("unknown")}</Badge>
              </div>
              <p className="text-muted-foreground text-sm">{t("provisionHelp")}</p>
              <p className="text-muted-foreground text-sm">{t("defaultHelp")}</p>
              <dl className="grid gap-3 text-sm sm:grid-cols-2">
                {[
                  [t("zone"), d.zone],
                  [t("driver"), d.dnsDriver],
                  [t("defaultFor"), d.defaultFor],
                  [t("wildcard"), d.isWildcardManaged ? t("yes") : t("no")],
                  [t("organization"), d.organizationSlug ?? t("unknown")],
                  [t("created"), fmt.formatDateTime(d.createdAt)],
                ].map(([name, value]) => (
                  <div key={name}>
                    <dt className="text-muted-foreground">{name}</dt>
                    <dd className="break-all">{value}</dd>
                  </div>
                ))}
              </dl>
            </section>
            <section className="space-y-3 rounded-lg border p-4">
              <h2 className="font-semibold">{t("challenge")}</h2>
              <p className="text-muted-foreground text-sm">{t("challengeHelp")}</p>
              <div>
                <h3 className="text-xs font-medium">{t("challengeName")}</h3>
                {values(d.challengeRecordName ? [d.challengeRecordName] : [])}
              </div>
              <div>
                <h3 className="text-xs font-medium">{t("challengeValue")}</h3>
                {values(d.challengeRecordValue ? [d.challengeRecordValue] : [])}
              </div>
            </section>
            {checks}
          </>
        )}
        {tab === "records" && (
          <>
            <div className="flex flex-wrap items-end gap-3">
              <div className="min-w-48 flex-1 space-y-1">
                <Label htmlFor={`${tabId}-search`}>{t("recordSearch")}</Label>
                <Input
                  id={`${tabId}-search`}
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor={`${tabId}-filter`}>{t("recordType")}</Label>
                <select
                  id={`${tabId}-filter`}
                  className="bg-background h-8 rounded-md border px-2 text-sm"
                  value={filter}
                  onChange={(e) => setFilter(e.target.value)}
                >
                  <option value="all">{t("all")}</option>
                  {recordTypes.map((value) => (
                    <option key={value}>{value}</option>
                  ))}
                </select>
              </div>
              <Button onClick={props.onRefreshDiagnostics} disabled={busy}>
                {t("refresh")}
              </Button>
            </div>
            {observation ? (
              <section className="space-y-3">
                <div className="flex flex-wrap items-center gap-2">
                  <h2 className="font-semibold">{t("providerZone")}</h2>
                  {state(observation.providerZone.state)}
                </div>
                {meta(d.dnsDriver, observation.providerZone.checkedAt)}
                {reason(observation.providerZone.reason)}
                <dl className="grid gap-2 text-sm sm:grid-cols-2">
                  <div>
                    <dt>{t("zone")}</dt>
                    <dd>{observation.providerZone.zoneName ?? t("unknown")}</dd>
                  </div>
                  <div>
                    <dt>{t("zoneId")}</dt>
                    <dd className="break-all">{observation.providerZone.zoneId ?? t("unknown")}</dd>
                  </div>
                  <div>
                    <dt>{t("visibility")}</dt>
                    <dd>
                      {typeof observation.providerZone.privateZone !== "boolean"
                        ? t("unknown")
                        : observation.providerZone.privateZone
                          ? t("privateZone")
                          : t("publicZone")}
                    </dd>
                  </div>
                  <div>
                    <dt>{t("bindingSource")}</dt>
                    <dd>
                      {observation.providerZone.bindingSource === "PLATFORM_WRITTEN"
                        ? t("platformBinding")
                        : observation.providerZone.bindingSource === "OPERATOR_CONFIGURATION"
                          ? t("operatorBinding")
                          : observation.providerZone.bindingSource ===
                              "PROTECTED_CLOUDFLARE_CONNECTION"
                            ? t("cloudflareBinding")
                            : t("unknown")}
                    </dd>
                  </div>
                </dl>
                {observation.providerZone.truncated && <p role="status">{t("recordsTruncated")}</p>}
                <p className="text-muted-foreground text-xs">
                  {t("recordCount", { count: records.length })}
                </p>
                <ul className="divide-y rounded-lg border" aria-label={t("records")}>
                  {records.map((r, i) => (
                    <li
                      key={`${r.name}:${r.type}:${i}`}
                      className="grid min-w-0 gap-3 p-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,2fr)]"
                    >
                      <dl className="space-y-1 text-sm">
                        <div>
                          <dt className="text-muted-foreground">{t("name")}</dt>
                          <dd className="font-mono text-xs break-all">{r.name}</dd>
                        </div>
                        <div>
                          <dt className="sr-only">{t("recordType")}</dt>
                          <dd>
                            <Badge variant="outline">{r.type}</Badge>
                          </dd>
                        </div>
                        <div>
                          <dt className="text-muted-foreground">{t("ttl")}</dt>
                          <dd>{r.ttl ?? t("unknown")}</dd>
                        </div>
                        {typeof r.proxied === "boolean" && (
                          <div>
                            <dt>{connectionText("proxied")}</dt>
                            <dd>{r.proxied ? t("yes") : t("no")}</dd>
                          </div>
                        )}
                        {r.priority != null && (
                          <div>
                            <dt>{t("priority")}</dt>
                            <dd>{r.priority}</dd>
                          </div>
                        )}
                      </dl>
                      <div className="space-y-2">
                        <h3 className="text-xs font-medium">{t("value")}</h3>
                        {values(r.values)}
                        {r.aliasTarget && (
                          <div>
                            <h3 className="text-xs font-medium">{t("alias")}</h3>
                            {values([r.aliasTarget])}
                            <p className="text-xs">
                              {t("aliasHealth")}:{" "}
                              {typeof r.evaluateTargetHealth !== "boolean"
                                ? t("unknown")
                                : r.evaluateTargetHealth
                                  ? t("yes")
                                  : t("no")}
                            </p>
                          </div>
                        )}
                      </div>
                    </li>
                  ))}
                </ul>
                {!records.length && (
                  <p>
                    {observation.providerZone.state === "OK" ? t("noRecords") : t("unknownHelp")}
                  </p>
                )}
              </section>
            ) : (
              !props.diagnosticsLoading && <p>{t("unknownHelp")}</p>
            )}
          </>
        )}
        {tab === "routing" && (
          <>
            <p className="text-muted-foreground text-sm">{t("routeHelp")}</p>
            {observation?.routesTruncated && <p role="status">{t("routesTruncated")}</p>}
            {observation?.routes.map((route) => (
              <section key={route.environmentId} className="space-y-3 rounded-lg border p-4">
                <h2 className="font-semibold">
                  <Link
                    className="underline"
                    href={`/apps/${encodeURIComponent(route.appSlug)}/domains`}
                  >
                    {route.appName}
                  </Link>{" "}
                  · {route.environmentName}
                </h2>
                {state(route.observedState)}
                <dl className="grid gap-3 text-sm sm:grid-cols-2">
                  <div>
                    <dt>{t("hostname")}</dt>
                    <dd>{values([route.hostname])}</dd>
                  </div>
                  <div>
                    <dt>{t("routingTarget")}</dt>
                    <dd className="break-all">{route.recordedUrl || t("unknown")}</dd>
                  </div>
                  <div>
                    <dt>{t("cluster")}</dt>
                    <dd>
                      {route.clusterSlug ? (
                        <Link
                          className="underline"
                          href={`/clusters/${encodeURIComponent(route.clusterSlug)}`}
                        >
                          {route.clusterName ?? route.clusterSlug}
                        </Link>
                      ) : (
                        t("unknown")
                      )}
                    </dd>
                  </div>
                  <div>
                    <dt>{t("ingress")}</dt>
                    <dd>{route.ingressClass ?? t("unknown")}</dd>
                  </div>
                  <div>
                    <dt>{t("tls")}</dt>
                    <dd>{t("unknown")}</dd>
                  </div>
                </dl>
              </section>
            ))}
            {!observation?.routes.length && <p>{observation ? t("noRoutes") : t("unknownHelp")}</p>}
          </>
        )}
        {tab === "diagnostics" && (
          <>
            <p className="text-muted-foreground text-sm">{t("probeHelp")}</p>
            <form
              className="flex flex-wrap items-end gap-3"
              onSubmit={(event) => {
                event.preventDefault();
                void props.onProbe({ hostname, tool, recordType });
              }}
            >
              <div className="min-w-48 flex-1 space-y-1">
                <Label htmlFor={`${tabId}-host`}>{t("hostname")}</Label>
                <Input
                  id={`${tabId}-host`}
                  value={hostname}
                  onChange={(e) => changeProbe(() => setHostname(e.target.value))}
                  required
                  maxLength={253}
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor={`${tabId}-tool`}>{t("tool")}</Label>
                <select
                  id={`${tabId}-tool`}
                  value={tool}
                  className="bg-background h-8 rounded-md border px-2 text-sm"
                  onChange={(e) => {
                    const value = tools.find((v) => v === e.target.value);
                    if (value) changeProbe(() => setTool(value));
                  }}
                >
                  {tools.map((value) => (
                    <option key={value} value={value}>
                      {t(toolKeys[value])}
                    </option>
                  ))}
                </select>
              </div>
              <div className="space-y-1">
                <Label htmlFor={`${tabId}-type`}>{t("recordType")}</Label>
                <select
                  id={`${tabId}-type`}
                  value={recordType}
                  className="bg-background h-8 rounded-md border px-2 text-sm"
                  onChange={(e) => {
                    const value = recordTypes.find((v) => v === e.target.value);
                    if (value) changeProbe(() => setRecordType(value));
                  }}
                >
                  {recordTypes.map((value) => (
                    <option key={value}>{value}</option>
                  ))}
                </select>
              </div>
              <Button type="submit" disabled={busy || props.probeLoading || !hostname.trim()}>
                {props.probeLoading ? t("running") : t("run")}
              </Button>
            </form>
            {props.probeError && <p role="alert">{props.probeError}</p>}
            {props.probe ? (
              <section className="space-y-3 rounded-lg border p-4" aria-label={t("response")}>
                {state(props.probe.state, props.probe.reason)}
                {meta(props.probe.perspective, props.probe.checkedAt)}
                {reason(props.probe.reason)}
                {values(props.probe.values)}
                <dl className="grid gap-2 text-sm sm:grid-cols-2">
                  {[
                    [t("address"), props.probe.publicAddress],
                    [t("httpStatus"), props.probe.httpStatus],
                    [
                      t("tlsVerified"),
                      typeof props.probe.tlsVerified !== "boolean"
                        ? null
                        : props.probe.tlsVerified
                          ? t("yes")
                          : t("no"),
                    ],
                    [t("latency"), props.probe.latencyMs],
                  ].map(([label, value]) => (
                    <div key={String(label)}>
                      <dt>{label}</dt>
                      <dd>{value ?? t("unknown")}</dd>
                    </div>
                  ))}
                </dl>
              </section>
            ) : (
              !props.probeLoading && <p>{t("emptyProbe")}</p>
            )}
            {checks}
          </>
        )}
      </div>
    </div>
  );
}

export function ManagedDomainDetail(props: ManagedDomainDetailProps) {
  const mail = useTranslations("emailDelivery");
  const t = useTranslations("managedDomains");
  return (
    <PageShell
      title={props.domain?.zone ?? t("title")}
      description={
        <Link href="/domains" className="underline">
          {t("back")}
        </Link>
      }
      actions={
        <div className="flex gap-2">
          {props.domain ? (
            <Button variant="outline" asChild>
              <Link href={`/domains/${props.domain.id}/email`}>{mail("domainTitle")}</Link>
            </Button>
          ) : null}
          <Button onClick={props.onRetry} disabled={props.loading}>
            {t("refresh")}
          </Button>
        </div>
      }
    >
      {props.error ? (
        <div role="alert">
          <p>{t("readFailed")}</p>
          <p>{props.error}</p>
        </div>
      ) : props.loading && !props.domain ? (
        <p role="status">{t("loading")}</p>
      ) : !props.domain ? (
        <p>{t("notFound")}</p>
      ) : (
        <DetailTabs
          key={`${props.domain.id}:${props.domain.version}`}
          {...props}
          domain={props.domain}
        />
      )}
    </PageShell>
  );
}
