"use client";

import { useRef, useState, useId, useLayoutEffect, type ReactNode } from "react";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useForm, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { LinkIcon } from "lucide-react";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { ListPage } from "@/components/list/ListPage";
import type { ListStateController } from "@/components/list/list-state";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export type SubscriptionStatus = "pending" | "active" | "revoking" | "revoked" | "failed";
export type ModelSubscription = {
  id: string;
  version: number;
  alias: string;
  bindingPrefix: string | null;
  appSlug: string;
  environmentName: string;
  status: SubscriptionStatus;
  desiredRevision: number;
  appliedRevision: number | null;
  reason: string | null;
  canRevoke: boolean;
};
export type SubscriptionTarget = {
  id: string;
  version: number;
  clusterId: string;
  appSlug: string;
  environmentName: string;
  admission: "allowed" | "denied" | "unknown";
  reason: string | null;
};
export type SubscriptionModel = {
  id: string;
  version: number;
  organizationId: string;
  name: string;
  runtimeAdmission: "configured" | "unsupported" | "unknown";
  nativeConnection?: boolean;
  nativeConfigured?: boolean;
  clusterId: string;
  providerId: string;
  subscriptionsEnabled: boolean;
};
export type SubscriptionRequest = {
  organizationId: string;
  modelId: string;
  modelVersion: number;
  expectedClusterId: string;
  expectedProviderId: string;
  environmentId: string;
  environmentVersion: number;
  alias: string;
};
export type RevokeSubscriptionRequest = {
  organizationId: string;
  modelId: string;
  modelVersion: number;
  expectedClusterId: string;
  expectedProviderId: string;
  subscriptionId: string;
  subscriptionVersion: number;
};
export type SubscriptionActionResult =
  | {
      accepted: true;
      state?: "pending" | "revoked";
      subscriptionId?: string;
      desiredRevision?: number;
    }
  | { accepted: false; message: string };
export type ModelPage<T> = {
  list: ListStateController;
  rows: T[];
  loading: boolean;
  stale: boolean;
  error: { message: string } | null;
  onRetry: () => void;
  nextCursor: string | null;
  totalCount: number | null;
};
export interface ModelSubscriptionsPanelProps {
  mode?: "legacy" | "connections";
  deployment: SubscriptionModel;
  targets: ModelPage<SubscriptionTarget>;
  subscriptions: ModelPage<ModelSubscription>;
  onSubscribe: (request: SubscriptionRequest) => Promise<SubscriptionActionResult>;
  onRevoke: (request: RevokeSubscriptionRequest) => Promise<SubscriptionActionResult>;
  onAccepted?: () => void;
  onSelectUsage?: (subscription: ModelSubscription) => void;
  usageBlocked?: boolean;
  usage?: ReactNode;
}

type Review = { scopeRevision: number; modelName: string } & (
  | { kind: "subscribe"; request: SubscriptionRequest; target: SubscriptionTarget }
  | { kind: "revoke"; request: RevokeSubscriptionRequest; subscription: ModelSubscription }
);
const draftSchema = z.object({
  environmentId: z.string().min(1),
  alias: z.string().regex(/^[a-z][a-z0-9_]{0,31}$/),
});

export function ModelSubscriptionsPanel(props: ModelSubscriptionsPanelProps) {
  return (
    <SubscriptionPanel
      key={`${props.deployment.organizationId}:${props.deployment.id}:${props.mode ?? "legacy"}`}
      {...props}
    />
  );
}

function SubscriptionPanel(props: ModelSubscriptionsPanelProps) {
  const { deployment, targets, subscriptions } = props;
  const targetsLoading = targets.loading;
  const targetsError = Boolean(targets.error);
  const t = useTranslations("models.shared.subscriptions");
  const inventory = useTranslations("models.shared.inventory");
  const confirmedPage = !subscriptions.loading && !subscriptions.stale && !subscriptions.error;
  const visibleApps = confirmedPage
    ? [...new Set(subscriptions.rows.map((row) => row.appSlug))]
    : [];

  const traffic = useTranslations("models.shared.subscriptionUsage");
  const id = useId();
  const form = useForm<z.infer<typeof draftSchema>>({
    resolver: zodResolver(draftSchema),
    defaultValues: { environmentId: "", alias: "" },
  });
  const environmentId = useWatch({ control: form.control, name: "environmentId" });
  const selectedTarget = targets.rows.find((row) => row.id === environmentId);
  const [review, setReview] = useState<Review | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [accepted, setAccepted] = useState(false);
  const [acceptedState, setAcceptedState] = useState<"pending" | "revoked">("pending");
  const [acceptedOperation, setAcceptedOperation] = useState<{
    id: string;
    revision: number;
    kind: "subscribe" | "revoke";
  } | null>(null);
  const confirmedOperation =
    acceptedOperation &&
    !subscriptions.stale &&
    !subscriptions.error &&
    subscriptions.rows.some(
      (row) =>
        row.id === acceptedOperation.id &&
        row.desiredRevision === acceptedOperation.revision &&
        row.appliedRevision != null &&
        row.appliedRevision >= row.desiredRevision &&
        row.status === (acceptedOperation.kind === "subscribe" ? "active" : "revoked")
    );
  const scopeKey = JSON.stringify([
    deployment,
    targets.rows,
    targetsLoading,
    targetsError,
    targets.stale,
    subscriptions.stale,
    subscriptions.loading,
    subscriptions.rows.map((row) => [row.id, row.version, row.canRevoke]),
  ]);
  const [scope, setScope] = useState({ key: scopeKey, revision: 0 });
  if (scope.key !== scopeKey) setScope({ key: scopeKey, revision: scope.revision + 1 });
  const lifecycle = useRef(0);
  const mounted = useRef(true);
  const latest = useRef({ props, revision: scope.revision });
  useLayoutEffect(() => {
    latest.current = { props, revision: scope.revision };
  });
  useLayoutEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      lifecycle.current += 1;
    };
  }, []);
  const reviewCurrent = review ? reviewIsCurrent(review, props, scope.revision) : false;
  async function confirm() {
    if (
      !review ||
      !mounted.current ||
      !reviewIsCurrent(review, latest.current.props, latest.current.revision)
    ) {
      setFailure(t("changed"));
      return false;
    }
    setFailure(null);
    const candidate = review,
      lifecycleRevision = lifecycle.current;
    try {
      const result =
        candidate.kind === "subscribe"
          ? await latest.current.props.onSubscribe(candidate.request)
          : await latest.current.props.onRevoke(candidate.request);
      if (
        !mounted.current ||
        lifecycleRevision !== lifecycle.current ||
        candidate.scopeRevision !== latest.current.revision ||
        latest.current.props.deployment.id !== candidate.request.modelId ||
        latest.current.props.deployment.organizationId !== candidate.request.organizationId
      )
        return false;
      if (result.accepted !== true) {
        setFailure(result.message);
        return false;
      }
      setAccepted(true);
      setAcceptedState(result.state ?? "pending");
      setAcceptedOperation(
        result.subscriptionId && result.desiredRevision != null
          ? { id: result.subscriptionId, revision: result.desiredRevision, kind: candidate.kind }
          : null
      );
      try {
        latest.current.props.onAccepted?.();
      } catch {
        /* Read refresh cannot undo an accepted write. */
      }
      return true;
    } catch {
      if (
        !mounted.current ||
        lifecycleRevision !== lifecycle.current ||
        candidate.scopeRevision !== latest.current.revision
      )
        return false;
      setFailure(t("requestFailed"));
      return false;
    }
  }
  const ready =
    deployment.subscriptionsEnabled &&
    (deployment.runtimeAdmission === "configured" || deployment.nativeConfigured === true) &&
    !targetsLoading &&
    !targetsError &&
    !targets.stale;
  return (
    <section
      id="model-connections"
      className="scroll-mt-20 space-y-5"
      aria-labelledby={`${id}-title`}
    >
      <div className="space-y-2">
        <h2 id={`${id}-title`} className="text-lg font-semibold">
          {inventory("connections")}
        </h2>
        <p className="text-muted-foreground text-sm">{inventory("connectionsHelp")}</p>
        {confirmedPage && subscriptions.totalCount != null && (
          <p>
            {inventory("visibleSubscriptions", { count: subscriptions.totalCount })} ·{" "}
            {inventory("shownApps", { count: visibleApps.length })}
          </p>
        )}
        {visibleApps.length > 0 && (
          <div className="flex flex-wrap gap-2">
            {visibleApps.map((app) => (
              <Button key={app} variant="outline" size="sm" asChild>
                <Link href={`/apps/${encodeURIComponent(app)}`}>{app}</Link>
              </Button>
            ))}
          </div>
        )}
        <p className="text-muted-foreground text-sm">{t("description")}</p>
      </div>
      <p className="border-warning-border bg-warning-bg text-warning-fg rounded-md border p-3 text-sm">
        {t("restartImpact")}
      </p>
      {!deployment.subscriptionsEnabled && <p role="status">{t("subscriptionsDisabled")}</p>}
      {deployment.nativeConnection && <p role="status">{inventory("nativeConnectionHelp")}</p>}
      {!deployment.nativeConnection && deployment.runtimeAdmission !== "configured" && (
        <p role="status">
          {t(
            deployment.runtimeAdmission === "unsupported" ? "unsupportedRuntime" : "unknownRuntime"
          )}
        </p>
      )}
      {props.mode !== "connections" && (
        <>
          <div className="space-y-2 sm:col-span-2">
            <h3 className="text-sm font-medium">{t("target")}</h3>
            <ListPage
              embedded
              label={t("target")}
              {...targets}
              getRowId={(row) => row.id}
              empty={{ icon: <LinkIcon />, title: t("noEligibleTargets") }}
              columns={[
                {
                  id: "environment",
                  header: t("target"),
                  cell: (target) => (
                    <Button
                      type="button"
                      variant="ghost"
                      className="h-auto text-left break-all whitespace-normal"
                      disabled={
                        !ready ||
                        target.admission !== "allowed" ||
                        target.clusterId !== deployment.clusterId
                      }
                      onClick={() =>
                        form.setValue("environmentId", target.id, { shouldValidate: true })
                      }
                    >
                      {target.appSlug} / {target.environmentName}
                    </Button>
                  ),
                },
                {
                  id: "admission",
                  header: t("state"),
                  cell: (target) =>
                    target.clusterId === deployment.clusterId && target.admission === "allowed"
                      ? t("eligible")
                      : (target.reason ?? t("notVerified")),
                },
              ]}
            />
            <p role="status" className="text-sm">
              {selectedTarget
                ? t("selectedTarget", {
                    target: `${selectedTarget.appSlug} / ${selectedTarget.environmentName}`,
                  })
                : t("chooseTarget")}
            </p>
            {form.formState.errors.environmentId && (
              <p role="alert" className="text-destructive text-sm">
                {t("chooseTarget")}
              </p>
            )}
          </div>
          <form
            className="grid gap-4 sm:grid-cols-2"
            onSubmit={form.handleSubmit((draft) => {
              const target = targets.rows.find(
                (item) =>
                  item.id === draft.environmentId &&
                  item.admission === "allowed" &&
                  item.clusterId === deployment.clusterId
              );
              if (!ready || !target) {
                setFailure(t("changed"));
                return;
              }
              setFailure(null);
              setAccepted(false);
              setReview({
                kind: "subscribe",
                scopeRevision: scope.revision,
                modelName: deployment.name,
                target,
                request: {
                  organizationId: deployment.organizationId,
                  modelId: deployment.id,
                  modelVersion: deployment.version,
                  expectedClusterId: deployment.clusterId,
                  expectedProviderId: deployment.providerId,
                  environmentId: target.id,
                  environmentVersion: target.version,
                  alias: draft.alias,
                },
              });
            })}
          >
            <div className="space-y-2">
              <Label htmlFor={`${id}-alias`}>{t("alias")}</Label>
              <Input
                id={`${id}-alias`}
                {...form.register("alias")}
                disabled={!ready}
                aria-invalid={Boolean(form.formState.errors.alias)}
                aria-describedby={`${id}-alias-help`}
                maxLength={32}
              />
              <p id={`${id}-alias-help`} className="text-muted-foreground text-sm">
                {t("aliasHelp")}
              </p>
              {form.formState.errors.alias && (
                <p role="alert" className="text-destructive text-sm">
                  {t("invalidAlias")}
                </p>
              )}
            </div>
            <div className="sm:col-span-2">
              <Button type="submit" disabled={!ready}>
                {t("reviewSubscribe")}
              </Button>
            </div>
          </form>
        </>
      )}
      {accepted && (
        <p role="status" className="text-sm">
          {t(
            acceptedState === "revoked" ||
              (confirmedOperation && acceptedOperation?.kind === "revoke")
              ? "revokedConfirmed"
              : confirmedOperation
                ? "accessConfirmed"
                : "accepted"
          )}
        </p>
      )}
      {failure && !review && (
        <p role="alert" className="text-destructive text-sm">
          {failure}
        </p>
      )}
      <ListPage
        embedded
        label={t("title")}
        {...subscriptions}
        getRowId={(row) => row.id}
        empty={{ icon: <LinkIcon />, title: t("empty"), description: t("emptyDescription") }}
        columns={[
          {
            id: "destination",
            header: t("destination"),
            cell: (row) => (
              <span className="break-all">
                {row.appSlug} / {row.environmentName}
              </span>
            ),
          },
          {
            id: "alias",
            header: t("alias"),
            cell: (row) => (
              <div>
                <code>{row.alias || t("legacyAlias")}</code>
                {row.bindingPrefix && (
                  <p className="text-muted-foreground text-xs break-all">{row.bindingPrefix}</p>
                )}
              </div>
            ),
          },
          {
            id: "status",
            header: t("state"),
            cell: (row) => (
              <div className="space-y-1">
                <p>{t(`status.${row.status}`)}</p>
                <p className="text-muted-foreground text-xs">
                  {t("revisions", {
                    desired: row.desiredRevision,
                    applied: row.appliedRevision ?? t("notObserved"),
                  })}
                </p>
                {row.reason && <p className="text-destructive text-sm break-words">{row.reason}</p>}
              </div>
            ),
          },
          ...(props.onSelectUsage
            ? [
                {
                  id: "traffic",
                  header: traffic("title"),
                  cell: (row: ModelSubscription) => (
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      disabled={!confirmedPage || props.usageBlocked}
                      onClick={() => props.onSelectUsage?.(row)}
                    >
                      {traffic("view")}
                    </Button>
                  ),
                },
              ]
            : []),
          {
            id: "actions",
            header: t("actions"),
            cell: (row) => (
              <Button
                variant="outline"
                size="sm"
                disabled={
                  !row.canRevoke ||
                  subscriptions.stale ||
                  (deployment.runtimeAdmission !== "configured" && !deployment.nativeConnection)
                }
                onClick={() => {
                  setFailure(null);
                  setAccepted(false);
                  setReview({
                    kind: "revoke",
                    scopeRevision: scope.revision,
                    modelName: deployment.name,
                    subscription: row,
                    request: {
                      organizationId: deployment.organizationId,
                      modelId: deployment.id,
                      modelVersion: deployment.version,
                      expectedClusterId: deployment.clusterId,
                      expectedProviderId: deployment.providerId,
                      subscriptionId: row.id,
                      subscriptionVersion: row.version,
                    },
                  });
                }}
              >
                {t("reviewRevoke")}
              </Button>
            ),
          },
        ]}
      />
      {props.usage}
      <ConfirmDialog
        open={Boolean(review)}
        onOpenChange={(open) => {
          if (!open) {
            setReview(null);
            setFailure(null);
          }
        }}
        title={t(review?.kind === "revoke" ? "revokeTitle" : "subscribeTitle", {
          name: review?.modelName ?? deployment.name,
        })}
        description={
          <span className="space-y-3">
            <span className="block break-words">
              {review?.kind === "subscribe"
                ? t("subscribeDestination", {
                    app: review.target.appSlug,
                    environment: review.target.environmentName,
                    alias: review.request.alias || t("legacyAlias"),
                  })
                : review &&
                  t("revokeDestination", {
                    app: review.subscription.appSlug,
                    environment: review.subscription.environmentName,
                    alias: review.subscription.alias || t("legacyAlias"),
                  })}
            </span>
            <span className="block">{t("restartImpact")}</span>
            {review && !reviewCurrent && (
              <span role="alert" className="text-destructive block">
                {t("changed")}
              </span>
            )}
            {failure && (
              <span role="alert" className="text-destructive block">
                {failure}
              </span>
            )}
          </span>
        }
        destructive={review?.kind === "revoke"}
        confirmLabel={t(review?.kind === "revoke" ? "confirmRevoke" : "confirmSubscribe")}
        confirmDisabled={Boolean(review && !reviewCurrent)}
        onConfirm={confirm}
      />
    </section>
  );
}

function reviewIsCurrent(
  candidate: Review,
  current: ModelSubscriptionsPanelProps,
  revision: number
) {
  if (
    candidate.scopeRevision !== revision ||
    current.deployment.organizationId !== candidate.request.organizationId ||
    current.deployment.id !== candidate.request.modelId ||
    current.deployment.version !== candidate.request.modelVersion ||
    current.deployment.clusterId !== candidate.request.expectedClusterId ||
    current.deployment.providerId !== candidate.request.expectedProviderId ||
    (current.deployment.runtimeAdmission !== "configured" && !current.deployment.nativeConnection)
  )
    return false;
  if (candidate.kind === "subscribe")
    return (
      (current.deployment.runtimeAdmission === "configured" ||
        current.deployment.nativeConfigured === true) &&
      current.deployment.subscriptionsEnabled &&
      !current.targets.stale &&
      !current.targets.loading &&
      !current.targets.error &&
      current.targets.rows.some(
        (target) =>
          target.id === candidate.request.environmentId &&
          target.version === candidate.request.environmentVersion &&
          target.clusterId === candidate.request.expectedClusterId &&
          target.admission === "allowed"
      )
    );
  return (
    !current.subscriptions.stale &&
    !current.subscriptions.loading &&
    !current.subscriptions.error &&
    current.subscriptions.rows.some(
      (subscription) =>
        subscription.id === candidate.request.subscriptionId &&
        subscription.version === candidate.request.subscriptionVersion &&
        subscription.canRevoke
    )
  );
}
