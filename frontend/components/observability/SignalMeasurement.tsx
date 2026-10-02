"use client";

import { useTranslations } from "next-intl";

import type { AstroliftAppGoldenSignal } from "@/graphql/__generated__/schema";

const reasonKey = (reason: string) => {
  if (["ENVIRONMENT_NOT_FOUND", "WORKLOAD_NOT_FOUND", "TARGET_NOT_OWNED"].includes(reason))
    return "target";
  if (
    [
      "PARTIAL_MEMBERSHIP",
      "MEMBERSHIP_LIMIT_EXCEEDED",
      "PARTIAL_DATA",
      "AMBIGUOUS_SERIES",
      "INVALID_DATA",
    ].includes(reason)
  )
    return "partial";
  if (["NO_PODS", "MISSING_USAGE", "NO_DATA_YET"].includes(reason)) return "noData";
  if (["PROVIDER_ERROR", "QUERY_ERROR"].includes(reason)) return "readError";
  return reason;
};

/** The server reports effective scope independently for every signal. */
export function SignalMeasurement({ signal }: { signal: AstroliftAppGoldenSignal | undefined }) {
  const t = useTranslations("apps.observability.signalMeasurement");
  const measured = signal?.measurement;
  if (!measured) return <p className="text-muted-foreground mb-3 text-xs">{t("unreported")}</p>;
  const target = measured.target;
  return (
    <div
      className="text-muted-foreground mb-3 space-y-1 text-xs break-words"
      data-testid="signal-measurement"
    >
      <p>{t("scope", { scope: t(`scopes.${measured.effectiveScope}`) })}</p>
      <p>{t("source", { source: t(`sources.${measured.source}`) })}</p>
      <p>{t(`bases.${measured.identityBasis}`)}</p>
      <p>
        {target.environmentName ?? "—"} · {target.namespace ?? "—"}
        {target.workloadSlug ? ` · ${target.workloadSlug}` : ""}
      </p>
      {!measured.available && measured.unavailableReason && (
        <p className="text-warning-fg">{t(`reasons.${reasonKey(measured.unavailableReason)}`)}</p>
      )}
      {measured.measurementStart && <p>{t("since", { timestamp: measured.measurementStart })}</p>}
      {measured.membershipObservedAt && (
        <p>{t("observed", { timestamp: measured.membershipObservedAt })}</p>
      )}
      <details>
        <summary className="cursor-pointer">{t("identities")}</summary>
        <dl className="mt-1 space-y-1 font-mono">
          <div>
            <dt>{t("organization")}</dt>
            <dd>{target.organizationId}</dd>
          </div>
          <div>
            <dt>{t("app")}</dt>
            <dd>{target.appId}</dd>
          </div>
          <div>
            <dt>{t("environment")}</dt>
            <dd>{target.environmentId ?? "—"}</dd>
          </div>
          <div>
            <dt>{t("cluster")}</dt>
            <dd>{target.clusterId ?? "—"}</dd>
          </div>
          {target.workloadId && (
            <div>
              <dt>{t("workload")}</dt>
              <dd>{target.workloadId}</dd>
            </div>
          )}
          {measured.containers.map((container) => (
            <div key={`${container.podUid}:${container.containerId}`}>
              <dt>
                {container.podName} / {container.containerName}
              </dt>
              <dd>
                {container.podUid} / {container.containerId}
              </dd>
            </div>
          ))}
        </dl>
      </details>
    </div>
  );
}
