"use client";

import type { ReactNode } from "react";
import Link from "next/link";
import { useFormatter, useTranslations } from "next-intl";
import { PageShell } from "@/components/PageShell";
import { QueryError } from "@/components/QueryError";
import { Button } from "@/components/ui/button";
import { DefinitionList } from "@/components/ui/definition-list";
import { Section } from "@/components/ui/section";
import { Skeleton } from "@/components/ui/skeleton";
import type { ClusterModelFieldsFragment } from "@/graphql/__generated__/operations";
import { SHARED_MODEL_STATUSES } from "./shared-models-list";

export type SharedModelDetailScreenProps = {
  model: ClusterModelFieldsFragment | null;
  loading: boolean;
  stale: boolean;
  error: string | null;
  onRetry: () => void;
  subscriptions: ReactNode;
  observations: ReactNode;
  prompt: ReactNode;
};
export function SharedModelDetailScreen({
  model,
  loading,
  stale,
  error,
  onRetry,
  subscriptions,
  observations,
  prompt,
}: SharedModelDetailScreenProps) {
  const t = useTranslations("models.shared.detail"),
    common = useTranslations("models.shared.deployments"),
    format = useFormatter();
  const unknown = t("unknown");
  const time = (value: string | null | undefined) =>
    value && Number.isFinite(Date.parse(value))
      ? `${format.dateTime(new Date(value), { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" })} UTC`
      : unknown;
  const resourceItems = (value: ClusterModelFieldsFragment["appliedResources"]) => [
    { term: t("cpu"), description: value?.cpuRequest ?? unknown },
    { term: t("memory"), description: value?.memoryRequest ?? unknown },
    {
      term: t("gpu"),
      description: value?.gpuCount == null ? unknown : format.number(value.gpuCount),
    },
    {
      term: t("replicas"),
      description: value?.replicas == null ? unknown : format.number(value.replicas),
    },
    {
      term: t("cache"),
      description:
        value?.cpuKvCacheGiB == null ? unknown : `${format.number(value.cpuKvCacheGiB)} GiB`,
    },
  ];
  const confirmed = Boolean(
    model?.ready === true &&
    model.readinessObservedAt &&
    Number.isFinite(Date.parse(model.readinessObservedAt)) &&
    model.readinessGeneration != null &&
    model.readinessGeneration > 0
  );
  return (
    <PageShell
      title={model?.name ?? t("title")}
      description={t("description")}
      actions={
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" asChild>
            <Link href="/models">{t("back")}</Link>
          </Button>
          <Button variant="outline" onClick={onRetry} disabled={loading || (stale && !error)}>
            {t("refresh")}
          </Button>
        </div>
      }
    >
      <div className="space-y-6">
        {error && <QueryError title={t("readError")} error={error} />}
        {loading && !model ? (
          <div role="status" className="space-y-3">
            <p>{t("loading")}</p>
            <Skeleton className="h-32 w-full" />
          </div>
        ) : !model ? (
          !error && <p role="status">{t("missing")}</p>
        ) : (
          <>
            {stale && <p role="status">{t(error ? "staleFacts" : "refreshing")}</p>}
            <Section title={t("identity")}>
              <DefinitionList
                items={[
                  {
                    term: t("organization"),
                    description: <code className="break-all">{model.organizationId}</code>,
                  },
                  {
                    term: common("cluster"),
                    description: (
                      <span className="break-all">
                        {model.clusterName} · {model.clusterSlug}
                      </span>
                    ),
                  },
                  {
                    term: t("provider"),
                    description: <code className="break-all">{model.providerId}</code>,
                  },
                  {
                    term: common("model"),
                    description: <code className="break-all">{model.modelRepo || unknown}</code>,
                  },
                  {
                    term: t("revision"),
                    description: <code className="break-all">{model.revisionSha ?? unknown}</code>,
                  },
                  { term: t("version"), description: format.number(model.version) },
                  {
                    term: common("compute"),
                    description:
                      model.computeMode === "cpu" || model.computeMode === "gpu"
                        ? common(model.computeMode)
                        : unknown,
                  },
                  {
                    term: common("status"),
                    description: (
                      <span>
                        {(SHARED_MODEL_STATUSES as readonly string[]).includes(model.status)
                          ? common(`statuses.${model.status}`)
                          : model.status}
                        {model.reason && (
                          <span className="text-muted-foreground block break-words">
                            {model.reason}
                          </span>
                        )}
                      </span>
                    ),
                  },
                  {
                    term: t("runtime"),
                    description: (
                      <span>
                        {t(
                          model.runtimeSupported === true
                            ? "runtimeConfigured"
                            : model.runtimeSupported === false
                              ? "runtimeUnsupported"
                              : "runtimeUnknown"
                        )}
                        {model.runtimeReason && (
                          <span className="text-muted-foreground block break-words">
                            {model.runtimeReason}
                          </span>
                        )}
                      </span>
                    ),
                  },
                  {
                    term: t("readiness"),
                    description: (
                      <span>
                        {confirmed
                          ? t("confirmedAt", {
                              time: time(model.readinessObservedAt),
                              generation: model.readinessGeneration!,
                            })
                          : t("noConfirmation")}
                        <span className="text-muted-foreground block">{t("noLiveHealth")}</span>
                      </span>
                    ),
                  },
                  {
                    term: common("subscriptions"),
                    description: model.subscriptionsEnabled
                      ? common("enabled")
                      : common("disabled"),
                  },
                ]}
              />
            </Section>
            <div className="grid gap-6 lg:grid-cols-2">
              <Section title={t("desired")} description={t("resourcesHelp")}>
                <DefinitionList items={resourceItems(model.desiredResources)} />
              </Section>
              <Section title={t("applied")} description={t("resourcesHelp")}>
                {model.appliedResources ? (
                  <DefinitionList items={resourceItems(model.appliedResources)} />
                ) : (
                  <p>{t("notApplied")}</p>
                )}
              </Section>
            </div>
            <Section title={t("operation")}>
              <p>
                {t("subscriptionRevisions", {
                  desired: model.desiredSubscriptionRevision,
                  applied: model.appliedSubscriptionRevision,
                })}
              </p>
              <DefinitionList
                items={[
                  {
                    term: t("operationId"),
                    description: <code className="break-all">{model.operationId ?? unknown}</code>,
                  },
                  { term: t("operationStarted"), description: time(model.operationStartedAt) },
                  { term: t("operationCompleted"), description: time(model.operationCompletedAt) },
                ]}
              />
            </Section>
            {subscriptions}
            {observations}
            {prompt}
          </>
        )}
      </div>
    </PageShell>
  );
}
