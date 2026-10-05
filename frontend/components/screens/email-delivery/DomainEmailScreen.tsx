"use client";
import * as React from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { PageShell } from "@/components/PageShell";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useFormatters } from "@/lib/i18n/formatters";
import type { ManagedDomainState } from "@/components/screens/domains/use-managed-domain";
import type {
  EmailDeliveryAppsQuery,
  EmailDeliveryServicesQuery,
} from "@/graphql/__generated__/operations";
type AppChoice = EmailDeliveryAppsQuery["astroliftAppsPage"]["items"][number];
type ServiceChoice = EmailDeliveryServicesQuery["astroliftManagedServicesPage"]["items"][number];
export type DomainEmailScreenProps = {
  domain: ManagedDomainState["domain"];
  domainLoading: boolean;
  domainError: string | null;
  apps: AppChoice[];
  appsLoading: boolean;
  appsError: string | null;
  appSearch: string;
  onAppSearch: (value: string) => void;
  app: AppChoice | null;
  onApp: (id: string) => void;
  appsNext: boolean;
  appsPrevious: boolean;
  onNextApps: () => void;
  onPreviousApps: () => void;
  services: ServiceChoice[];
  servicesLoading: boolean;
  servicesError: string | null;
  service: ServiceChoice | null;
  onService: (id: string) => void;
  servicesNext: boolean;
  servicesPrevious: boolean;
  onNextServices: () => void;
  onPreviousServices: () => void;
  onRefresh: () => void;
  delivery: React.ReactNode;
  association: "match" | "different" | "unconfirmedDomain";
  probe: ManagedDomainState["probe"];
  probeLoading: boolean;
  probeError: string | null;
  onProbe: ManagedDomainState["onProbe"];
  onResetProbe: () => void;
};
const states = {
  OK: "ok",
  MISMATCH: "mismatch",
  UNKNOWN: "unknown",
  ERROR: "unavailable",
  UNSUPPORTED: "unsupported",
} as const;
/** Authorized app/service selection and actual bounded mail DNS observations. */
export function DomainEmailScreen(p: DomainEmailScreenProps) {
  const t = useTranslations("emailDelivery"),
    dns = useTranslations("managedDomains"),
    paging = useTranslations("domainConnections"),
    fmt = useFormatters();
  const [selector, setSelector] = React.useState("");
  const [dkimType, setDkimType] = React.useState<"TXT" | "CNAME">("TXT");
  const validSelector = /^[A-Za-z0-9_-]{1,63}$/.test(selector.trim());
  const zone = p.domain?.zone;
  const query = (hostname: string, recordType: "TXT" | "MX" | "CNAME") => {
    p.onResetProbe();
    void p.onProbe({ hostname, recordType, tool: "LOOKUP" });
  };
  return (
    <PageShell
      title={t("domainTitle")}
      description={p.domain ? <Link href={`/domains/${p.domain.id}`}>{p.domain.zone}</Link> : null}
      actions={
        <Button variant="outline" onClick={p.onRefresh}>
          {t("refresh")}
        </Button>
      }
    >
      {p.domainLoading ? (
        <p role="status">{dns("loading")}</p>
      ) : p.domainError ? (
        <p role="alert">{p.domainError}</p>
      ) : !p.domain ? (
        <p>{dns("notFound")}</p>
      ) : (
        <>
          <p className="text-muted-foreground text-sm">{t("domainHelp")}</p>
          <Card className="space-y-3 p-4">
            <div className="grid gap-3 md:grid-cols-2">
              <div className="space-y-2">
                <Label htmlFor="mail-app-search">{t("appSearch")}</Label>
                <Input
                  id="mail-app-search"
                  value={p.appSearch}
                  onChange={(e) => p.onAppSearch(e.target.value)}
                />
                <Label htmlFor="mail-app">{t("apps")}</Label>
                <select
                  id="mail-app"
                  className="bg-background h-9 w-full rounded-md border px-2"
                  value={p.app?.id ?? ""}
                  disabled={p.appsLoading || !!p.appsError}
                  onChange={(e) => p.onApp(e.target.value)}
                >
                  <option value="">{t("selectApp")}</option>
                  {p.app && !p.apps.some((row) => row.id === p.app!.id) ? (
                    <option value={p.app.id}>{p.app.name}</option>
                  ) : null}
                  {p.apps.map((row) => (
                    <option key={row.id} value={row.id}>
                      {row.name}
                    </option>
                  ))}
                </select>
                {p.appsError ? (
                  <p role="alert">{t("selectionUnavailable")}</p>
                ) : !p.appsLoading && !p.apps.length ? (
                  <p>{t("noApps")}</p>
                ) : null}
                <div className="flex gap-2">
                  <Button
                    variant="outline"
                    disabled={!p.appsPrevious || p.appsLoading}
                    onClick={p.onPreviousApps}
                  >
                    {paging("previousPage")}
                  </Button>
                  <Button
                    variant="outline"
                    disabled={!p.appsNext || p.appsLoading}
                    onClick={p.onNextApps}
                  >
                    {paging("nextPage")}
                  </Button>
                </div>
              </div>
              <div className="space-y-2">
                <Label htmlFor="mail-service">{t("services")}</Label>
                <select
                  id="mail-service"
                  className="bg-background h-9 w-full rounded-md border px-2"
                  value={p.service?.id ?? ""}
                  disabled={!p.app || p.servicesLoading || !!p.servicesError}
                  onChange={(e) => p.onService(e.target.value)}
                >
                  <option value="">{t("selectService")}</option>
                  {p.service && !p.services.some((row) => row.id === p.service!.id) ? (
                    <option value={p.service.id}>
                      {p.service.name} / {p.service.environmentName} ({p.service.variant})
                    </option>
                  ) : null}
                  {p.services.map((row) => (
                    <option key={row.id} value={row.id}>
                      {row.name || row.kind} / {row.environmentName} ({row.variant})
                    </option>
                  ))}
                </select>
                {p.servicesError ? (
                  <p role="alert">{t("selectionUnavailable")}</p>
                ) : p.app && !p.servicesLoading && !p.services.length ? (
                  <p>{t("noServices")}</p>
                ) : null}
                <div className="flex gap-2">
                  <Button
                    variant="outline"
                    disabled={!p.servicesPrevious || p.servicesLoading}
                    onClick={p.onPreviousServices}
                  >
                    {paging("previousPage")}
                  </Button>
                  <Button
                    variant="outline"
                    disabled={!p.servicesNext || p.servicesLoading}
                    onClick={p.onNextServices}
                  >
                    {paging("nextPage")}
                  </Button>
                </div>
              </div>
            </div>
            {p.service ? <p className="text-muted-foreground text-sm">{t(p.association)}</p> : null}
          </Card>
          {p.delivery}
          <Card className="space-y-3 p-4" aria-label={t("mailDns")}>
            <h2 className="font-semibold">{t("mailDns")}</h2>
            <p className="text-muted-foreground text-sm">{t("dnsHelp")}</p>
            <div className="flex flex-wrap gap-2">
              {(
                [
                  ["mx", zone!, "MX"],
                  ["spf", zone!, "TXT"],
                  ["dmarc", `_dmarc.${zone!}`, "TXT"],
                ] as const
              ).map(([key, host, type]) => (
                <Button
                  key={key}
                  variant="outline"
                  disabled={p.probeLoading}
                  onClick={() => query(host, type)}
                >
                  {t(key)}
                </Button>
              ))}
            </div>
            <div className="flex flex-wrap items-end gap-2">
              <div className="min-w-40 flex-1 space-y-1">
                <Label htmlFor="mail-dkim-selector">{t("selector")}</Label>
                <Input
                  id="mail-dkim-selector"
                  value={selector}
                  maxLength={63}
                  onChange={(e) => {
                    setSelector(e.target.value);
                    p.onResetProbe();
                  }}
                  autoComplete="off"
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="mail-dkim-type">{t("dkimRecordType")}</Label>
                <select
                  id="mail-dkim-type"
                  className="bg-background h-9 rounded-md border px-2"
                  value={dkimType}
                  onChange={(e) => {
                    setDkimType(e.target.value as "TXT" | "CNAME");
                    p.onResetProbe();
                  }}
                >
                  <option value="TXT">TXT</option>
                  <option value="CNAME">CNAME</option>
                </select>
              </div>
              <Button
                variant="outline"
                disabled={!validSelector || p.probeLoading}
                onClick={() => query(`${selector.trim()}._domainkey.${zone!}`, dkimType)}
              >
                {t("dkim")}
              </Button>
            </div>
            {selector && !validSelector ? <p role="alert">{t("invalidSelector")}</p> : null}
            {p.probeLoading ? (
              <p role="status">{t("lookupLoading")}</p>
            ) : p.probeError ? (
              <p role="alert">{t("lookupUnavailable")}</p>
            ) : p.probe ? (
              <div className="space-y-2">
                <p>
                  {dns(states[p.probe.state])}: <code>{p.probe.hostname}</code> (
                  {p.probe.recordType})
                </p>
                <dl className="grid gap-2 text-xs sm:grid-cols-2">
                  <div>
                    <dt>{t("perspective")}</dt>
                    <dd>{p.probe.perspective}</dd>
                  </div>
                  <div>
                    <dt>{t("checkedAt")}</dt>
                    <dd>{fmt.formatDateTime(p.probe.checkedAt)}</dd>
                  </div>
                </dl>
                {p.probe.reason ? <p className="font-mono text-xs">{p.probe.reason}</p> : null}
                {p.probe.values.length ? (
                  <ul aria-label={t("records")} className="space-y-1">
                    {p.probe.values.map((value, index) => (
                      <li key={`${index}:${value}`}>
                        <code className="text-xs break-all">{value}</code>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p>{t("recordsEmpty")}</p>
                )}
              </div>
            ) : null}
          </Card>
        </>
      )}
    </PageShell>
  );
}
