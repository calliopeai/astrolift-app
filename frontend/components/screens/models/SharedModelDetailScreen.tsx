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
import { modelSourceMode, nativeModelFamily } from "./native-model-source";
import { NativeConnectionMetadataPanel } from "./NativeConnectionMetadataPanel";
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
  management?: ReactNode;
  removalConfirmed?: boolean;
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
  management,
  removalConfirmed = false,
}: SharedModelDetailScreenProps) {
  const local = useTranslations("models.shared.localImport");
  const inventory = useTranslations("models.shared.inventory");
  const t = useTranslations("models.shared.detail"),
    common = useTranslations("models.shared.deployments"),
    format = useFormatter();
  const native = useTranslations("models.native.details"),
    connect = useTranslations("models.native.connect");
  const mode = model ? modelSourceMode(model) : "unsupported";
  const family = useTranslations("models.native.common");
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
      description={
        mode === "native"
          ? native("description")
          : mode === "native_unavailable"
            ? native("unavailable")
            : t("description")
      }
      actions={
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" asChild>
            <Link href="/models">{t("back")}</Link>
          </Button>
          {model && subscriptions && (
            <Button variant="outline" asChild>
              <a href="#model-connections">{inventory("connections")}</a>
            </Button>
          )}
          {model && observations && (
            <Button variant="outline" asChild>
              <a href="#model-metrics">{inventory("metrics")}</a>
            </Button>
          )}
          {model && management && (
            <Button variant="outline" asChild>
              <a href="#model-settings">{inventory("settings")}</a>
            </Button>
          )}
          <Button variant="outline" onClick={onRetry} disabled={loading || (stale && !error)}>
            {t("refresh")}
          </Button>
        </div>
      }
    >
      <div className="space-y-6">
        {removalConfirmed && <p role="status">{native("removed")}</p>}
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
            <Section title={inventory("overview")}>
              <DefinitionList
                items={
                  mode === "native" && model.nativeSource
                    ? [
                        {
                          term: common("cluster"),
                          description: `${model.clusterName} · ${model.clusterSlug}`,
                        },
                        { term: connect("provider"), description: "Amazon Bedrock" },
                        { term: connect("account"), description: model.nativeSource.accountId },
                        { term: connect("region"), description: model.nativeSource.region },
                        { term: native("partition"), description: model.nativeSource.partition },
                        {
                          term: inventory("source"),
                          description: (
                            <code className="break-all">{model.nativeSource.sourceArn}</code>
                          ),
                        },
                        {
                          term: connect("fingerprint"),
                          description: (
                            <code className="break-all">
                              {model.nativeSource.sourceFingerprint}
                            </code>
                          ),
                        },
                        {
                          term: native("observedAt"),
                          description: time(model.nativeSource.metadataObservedAt),
                        },
                        {
                          term: native("configuration"),
                          description: native(
                            model.nativeSource.configurationState === "configured"
                              ? "configured"
                              : "unavailable"
                          ),
                        },
                        {
                          term: inventory("access"),
                          description:
                            model.sharingMode === "SHARED"
                              ? inventory("shared")
                              : model.sharingMode === "DEDICATED"
                                ? inventory("dedicated")
                                : inventory("unknownAccess"),
                        },
                        ...(model.sharingMode === "DEDICATED"
                          ? [
                              {
                                term: inventory("selectApp"),
                                description:
                                  model.dedicatedAppName && model.dedicatedAppSlug ? (
                                    <Link
                                      href={`/apps/${encodeURIComponent(model.dedicatedAppSlug)}`}
                                    >
                                      {model.dedicatedAppName}
                                    </Link>
                                  ) : (
                                    inventory("unknownAccess")
                                  ),
                              },
                            ]
                          : []),
                        {
                          term: common("subscriptions"),
                          description: common(model.subscriptionsEnabled ? "enabled" : "disabled"),
                        },
                        {
                          term: common("status"),
                          description: (
                            <span>
                              {(SHARED_MODEL_STATUSES as readonly string[]).includes(model.status)
                                ? common(`statuses.${model.status}`)
                                : model.status}
                              {model.reason && (
                                <span className="block break-words">{model.reason}</span>
                              )}
                            </span>
                          ),
                        },
                      ]
                    : mode === "unsupported" || mode === "native_unavailable"
                      ? [
                          {
                            term: common("cluster"),
                            description: `${model.clusterName} · ${model.clusterSlug}`,
                          },
                          {
                            term: inventory("source"),
                            description: (
                              <code className="break-all">
                                {nativeModelFamily(model)
                                  ? family(nativeModelFamily(model)!)
                                  : unknown}
                              </code>
                            ),
                          },
                          { term: common("status"), description: model.status },
                        ]
                      : [
                          {
                            term: common("cluster"),
                            description: (
                              <span className="break-all">
                                {model.clusterName} · {model.clusterSlug}
                              </span>
                            ),
                          },
                          {
                            term:
                              model.sourceKind === "local_artifact"
                                ? local("servedIdentifier")
                                : common("model"),
                            description: (
                              <code className="break-all">{model.modelRepo || unknown}</code>
                            ),
                          },
                          {
                            term:
                              model.sourceKind === "local_artifact"
                                ? local("manifestRevision")
                                : t("revision"),
                            description: (
                              <code className="break-all">
                                {(model.sourceKind === "local_artifact"
                                  ? model.localManifestSha256
                                  : model.revisionSha) ?? unknown}
                              </code>
                            ),
                          },
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
                                    })
                                  : t("noConfirmation")}
                                <span className="text-muted-foreground block">
                                  {t("noLiveHealth")}
                                </span>
                              </span>
                            ),
                          },
                          {
                            term: inventory("access"),
                            description:
                              model.sharingMode === "SHARED" ? (
                                inventory("shared")
                              ) : (
                                <span>
                                  {inventory("dedicated")}
                                  <span className="block">
                                    {model.dedicatedAppSlug && model.dedicatedAppName ? (
                                      <Link
                                        href={`/apps/${encodeURIComponent(model.dedicatedAppSlug)}`}
                                      >
                                        {inventory("dedicatedApp", { app: model.dedicatedAppName })}
                                      </Link>
                                    ) : (
                                      inventory("unknownAccess")
                                    )}
                                  </span>
                                </span>
                              ),
                          },
                          {
                            term: common("subscriptions"),
                            description: model.subscriptionsEnabled
                              ? common("enabled")
                              : common("disabled"),
                          },
                        ]
                }
              />
            </Section>
            {mode === "unsupported" && <p role="status">{native("unsupported")}</p>}
            {(mode === "native" || mode === "native_unavailable") && (
              <NativeConnectionMetadataPanel model={model} />
            )}
            {mode === "native_unavailable" && model.reason && (
              <p role="status" className="break-words">
                {model.reason}
              </p>
            )}
            {mode === "hosted" && (
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
            )}
            <Section title={t("operation")}>
              <p>
                {t("subscriptionRevisions", {
                  desired: model.desiredSubscriptionRevision,
                  applied: model.appliedSubscriptionRevision,
                })}
              </p>
            </Section>
            {subscriptions}
            {observations}
            {prompt}
            {management}
            <details className="rounded-lg border p-4">
              <summary className="cursor-pointer font-medium">{t("technical")}</summary>
              <div className="mt-4 space-y-4">
                <DefinitionList
                  items={[
                    {
                      term: t("organization"),
                      description: <code className="break-all">{model.organizationId}</code>,
                    },
                    {
                      term: t("provider"),
                      description: <code className="break-all">{model.providerId}</code>,
                    },
                    { term: t("version"), description: format.number(model.version) },
                    {
                      term: t("generation"),
                      description:
                        model.readinessGeneration == null
                          ? unknown
                          : format.number(model.readinessGeneration),
                    },
                  ]}
                />
                <DefinitionList
                  items={[
                    {
                      term: t("operationId"),
                      description: (
                        <code className="break-all">{model.operationId ?? unknown}</code>
                      ),
                    },
                    { term: t("operationStarted"), description: time(model.operationStartedAt) },
                    {
                      term: t("operationCompleted"),
                      description: time(model.operationCompletedAt),
                    },
                  ]}
                />
              </div>
            </details>
          </>
        )}
      </div>
    </PageShell>
  );
}
