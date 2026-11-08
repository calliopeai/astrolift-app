"use client";

import { NetworkIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import type { Column, CursorTableController } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import { useCursorTableList } from "@/components/list/use-cursor-table-list";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { DefinitionList } from "@/components/ui/definition-list";
import { Section } from "@/components/ui/section";
import { Skeleton } from "@/components/ui/skeleton";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
} from "@/components/ui/select";
import type {
  GetNvkeProviderObservationQuery,
  GetNvkeProviderIdentitiesQuery,
} from "@/graphql/__generated__/operations";

export type NvkeProviderObservation = NonNullable<
  GetNvkeProviderObservationQuery["astroliftNvkeProviderObservation"]["data"]
>;
export type NvkeProviderIdentity = NonNullable<
  GetNvkeProviderIdentitiesQuery["astroliftNvkeProviderIdentitiesPage"]["data"]
>["items"][number];
export type NvkeProviderId = "nico" | "launchlayer";
export interface NvkeProvidersPanelProps {
  observation: NvkeProviderObservation | null;
  provider: NvkeProviderId;
  identities: CursorTableController<NvkeProviderIdentity>;
  loading: boolean;
  error: string | null;
  onRefresh: () => void;
  onProviderChange: (provider: NvkeProviderId) => void;
}
export function NvkeProvidersPanel(props: NvkeProvidersPanelProps) {
  const t = useTranslations("nvke.providers"),
    common = useTranslations("nvke");
  const list = useCursorTableList(props.identities, {
    id: "nvke.provider.identities",
    label: t("identities"),
    searchPlaceholder: t("search"),
  });
  const text = (value: string | null | undefined) =>
    value == null ? (
      t("unknown")
    ) : (
      <code className="text-xs [overflow-wrap:anywhere]">{value}</code>
    );
  const bool = (value: boolean | null | undefined) =>
    value == null ? t("unknown") : t(value ? "yes" : "no");
  const reference = (namespace: string | null | undefined, name: string | null | undefined) =>
    name && namespace ? text(namespace + "/" + name) : t("unknown");
  const columns: Column<NvkeProviderIdentity>[] = [
    { id: "namespace", header: t("namespace"), cell: (row) => text(row.namespace) },
    { id: "name", header: t("identity"), cell: (row) => text(row.name) },
  ];
  const data = !props.loading && !props.error ? props.observation : null;
  return (
    <Section title={t("title")} description={t("help")}>
      <div className="flex">
        <Button variant="outline" disabled={props.loading} onClick={props.onRefresh}>
          {common("refresh")}
        </Button>
      </div>
      {props.loading && <Skeleton className="h-32" />}
      {props.error && (
        <p role="alert" className="text-danger-fg text-sm">
          {props.error}
        </p>
      )}
      {!props.loading && !props.error && !data && <p>{t("empty")}</p>}
      {data && (
        <>
          <p role="note" className="text-muted-foreground text-sm">
            {common("historicalHelp")} <time dateTime={data.observedAt}>{data.observedAt}</time>
          </p>
          {!data.complete && (
            <p role="status" className="bg-warning-bg text-warning-fg rounded-lg p-3 text-sm">
              {t("partial")}
            </p>
          )}
          <DefinitionList
            items={[
              { term: t("source"), description: text(data.connectionId) },
              { term: t("sourceVersion"), description: data.connectionVersion },
              { term: t("siteVersion"), description: data.sourceSiteVersion },
              { term: t("revision"), description: text(data.upstreamRevision) },
            ]}
          />
          <div className="grid min-w-0 gap-4 xl:grid-cols-2">
            {data.providers.map((row) => (
              <Card key={row.provider} className="min-w-0">
                <CardHeader>
                  <CardTitle>{row.provider === "nico" ? "NICo" : "LaunchLayer"}</CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                  <Badge variant="outline">{t(`states.${row.state}`)}</Badge>
                  {row.unconfiguredIsValid && row.state === "NOT-CONFIGURED" && (
                    <p className="text-muted-foreground text-sm">{t("optional")}</p>
                  )}
                  <DefinitionList
                    items={[
                      { term: t("crds"), description: bool(row.crdsEstablished) },
                      { term: t("managerAvailable"), description: bool(row.controllerAvailable) },
                      {
                        term: t("manager"),
                        description: reference(row.managerNamespace, row.managerName),
                      },
                      { term: t("readyReplicas"), description: text(row.readyReplicas) },
                      { term: t("desiredReplicas"), description: text(row.desiredReplicas) },
                      {
                        term: t("secret"),
                        description: reference(row.secretNamespace, row.secretName),
                      },
                      { term: t("secretPresent"), description: bool(row.secretPresent) },
                      { term: t("endpoint"), description: bool(row.endpointConfigured) },
                    ]}
                  />
                  {row.credentials ? (
                    <>
                      <h3 className="text-base font-semibold">{t("credentials")}</h3>
                      <Badge variant="outline">{t(`health.${row.credentials.health}`)}</Badge>
                      <p className="text-muted-foreground text-sm">{t("healthHelp")}</p>
                      <DefinitionList
                        items={[
                          {
                            term: t("identity"),
                            description: reference(
                              row.credentials.identityNamespace,
                              row.credentials.identityName
                            ),
                          },
                          {
                            term: t("generation"),
                            description: text(row.credentials.identityGeneration),
                          },
                          {
                            term: t("observedGeneration"),
                            description: text(row.credentials.observedGeneration),
                          },
                          {
                            term: t("lastChecked"),
                            description: text(row.credentials.lastCheckedTime),
                          },
                          {
                            term: t("nextCheck"),
                            description: text(row.credentials.nextCheckTime),
                          },
                          {
                            term: t("additional"),
                            description: row.credentials.additionalIdentityCount,
                          },
                        ]}
                      />
                    </>
                  ) : (
                    <p className="text-muted-foreground text-sm">{t("noCheck")}</p>
                  )}
                  <details>
                    <summary className="cursor-pointer text-sm">{t("diagnostics")}</summary>
                    <DefinitionList
                      items={[
                        { term: t("reason"), description: text(row.reason) },
                        { term: t("credentialReason"), description: text(row.credentials?.reason) },
                        { term: t("readyStatus"), description: text(row.credentials?.readyStatus) },
                      ]}
                    />
                  </details>
                </CardContent>
              </Card>
            ))}
          </div>
          <div className="max-w-sm space-y-2">
            <Label htmlFor="nvke-provider-identities">{t("identityProvider")}</Label>
            <Select
              value={props.provider}
              onValueChange={(value) => {
                if (value === "nico" || value === "launchlayer") props.onProviderChange(value);
              }}
            >
              <SelectTrigger id="nvke-provider-identities" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="nico">NICo</SelectItem>
                <SelectItem value="launchlayer">LaunchLayer</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <ListPage
            embedded
            list={list}
            label={t("identities")}
            rows={props.identities.rows}
            columns={columns}
            getRowId={(row) => row.key}
            loading={props.identities.state === "loading"}
            stale={props.identities.isStale}
            error={props.identities.state === "error" ? { message: common("readFailed") } : null}
            onRetry={props.identities.retry}
            nextCursor={props.identities.nextCursor}
            totalCount={props.identities.totalCount}
            empty={{
              icon: <NetworkIcon />,
              title: t("noIdentities"),
              description: t("identityHelp"),
            }}
          />
        </>
      )}
    </Section>
  );
}
