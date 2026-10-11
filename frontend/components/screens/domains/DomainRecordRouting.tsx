"use client";

import { useTranslations } from "next-intl";

const policies = {
  SIMPLE: "simple",
  WEIGHTED: "weighted",
  LATENCY: "latency",
  FAILOVER: "failoverPolicy",
  GEOLOCATION: "geolocation",
  MULTIVALUE: "multivaluePolicy",
  GEOPROXIMITY: "geoproximity",
  CIDR: "cidrPolicy",
  UNKNOWN: "unknownPolicy",
} as const;

// Structural subset of the exported ManagedDomainRecordRouting DTO, without a query dependency.
export interface DomainRecordRoutingData {
  policy: keyof typeof policies;
  reason: string;
  setIdentifier?: string | null;
  weight?: number | null;
  region?: string | null;
  failover?: string | null;
  geoLocation?: {
    continentCode?: string | null;
    countryCode?: string | null;
    subdivisionCode?: string | null;
  } | null;
  geoProximity?: {
    awsRegion?: string | null;
    localZoneGroup?: string | null;
    coordinates?: { latitude: string; longitude: string } | null;
    bias?: number | null;
  } | null;
  cidr?: { collectionId: string; locationName: string } | null;
  multiValueAnswer?: boolean | null;
  healthCheckId?: string | null;
  trafficPolicyInstanceId?: string | null;
}

export interface DomainRecordRoutingProps {
  routing: DomainRecordRoutingData | null | undefined;
}

export function DomainRecordRouting({ routing }: DomainRecordRoutingProps) {
  const t = useTranslations("domainRecordRouting");
  if (!routing) return null;
  const policy = Object.prototype.hasOwnProperty.call(policies, routing.policy)
    ? policies[routing.policy]
    : "unknownPolicy";
  const rows: [string, string | number | null | undefined][] = [
    [t("identifier"), routing.setIdentifier],
    [t("weight"), routing.weight],
    [t("region"), routing.region],
    [t("failover"), routing.failover],
    [t("continent"), routing.geoLocation?.continentCode],
    [t("country"), routing.geoLocation?.countryCode],
    [t("subdivision"), routing.geoLocation?.subdivisionCode],
    [t("region"), routing.geoProximity?.awsRegion],
    [t("localZone"), routing.geoProximity?.localZoneGroup],
    [t("latitude"), routing.geoProximity?.coordinates?.latitude],
    [t("longitude"), routing.geoProximity?.coordinates?.longitude],
    [t("bias"), routing.geoProximity?.bias],
    [t("cidrCollection"), routing.cidr?.collectionId],
    [t("cidrLocation"), routing.cidr?.locationName],
    [
      t("multivalue"),
      routing.multiValueAnswer == null ? null : t(routing.multiValueAnswer ? "yes" : "no"),
    ],
    [t("healthCheck"), routing.healthCheckId],
    [t("trafficPolicy"), routing.trafficPolicyInstanceId],
  ];
  const reason =
    routing.reason === "AMBIGUOUS_ROUTING_POLICY"
      ? "ambiguous"
      : routing.reason === "INCOMPLETE_ROUTING_POLICY"
        ? "incomplete"
        : "unknownReason";
  return (
    <details className="min-w-0 rounded-md border p-3 text-sm">
      <summary className="cursor-pointer font-medium break-words">
        {t("title")}: {t(policy)}
      </summary>
      <div className="mt-3 space-y-3">
        <p className="text-muted-foreground">{t("help")}</p>
        {policy === "unknownPolicy" && <p role="note">{t(reason)}</p>}
        <dl className="grid min-w-0 grid-cols-1 gap-x-4 gap-y-2 sm:grid-cols-[minmax(0,1fr)_minmax(0,2fr)]">
          {rows
            .filter(([, value]) => value != null)
            .map(([label, value], index) => (
              <div className="contents" key={`${label}-${index}`}>
                <dt className="text-muted-foreground break-words">{label}</dt>
                <dd className="min-w-0 [overflow-wrap:anywhere]">{value}</dd>
              </div>
            ))}
        </dl>
      </div>
    </details>
  );
}
