"use client";
import { useLayoutEffect, useRef, useState, useId } from "react";
import { useTranslations } from "next-intl";
import type {
  ClusterModelFieldsFragment,
  UpdateClusterModelInput,
  DeprovisionClusterModelInput,
} from "@/graphql/__generated__/operations";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { SharedModelDraft } from "./shared-model-form";
import {
  modelAccessDraft,
  modelSettingsRequest,
  type ModelAccessDraft,
  type DedicatedModelApp,
} from "./shared-model-settings";
import { UsersIcon } from "lucide-react";
import { ListPage } from "@/components/list/ListPage";
import type { ModelPage } from "./ModelSubscriptionsPanel";
import type { ModelPlacementAdmission } from "./SharedModelDeploymentScreen";
export type ManagementResult =
  | { accepted: true; operationId: string }
  | { accepted: false; message: string };
export type SharedModelManagementPanelProps = {
  model: ClusterModelFieldsFragment;
  blocked: boolean;
  canManage: boolean;
  capabilityLoading: boolean;
  capabilityError: string | null;
  onRetryCapabilities: () => void;
  draft: SharedModelDraft;
  onDraftChange: <K extends keyof SharedModelDraft>(field: K, value: SharedModelDraft[K]) => void;
  access?: ModelAccessDraft;
  onAccessChange?: (mode: ModelAccessDraft["mode"]) => void;
  onSelectDedicatedApp?: (app: DedicatedModelApp) => void;
  dedicatedApps?: ModelPage<DedicatedModelApp>;
  admission: ModelPlacementAdmission | null;
  admissionLoading: boolean;
  admissionError: string | null;
  onRetryAdmission: () => void;
  onUpdate: (input: UpdateClusterModelInput) => Promise<ManagementResult>;
  onDelete: (input: DeprovisionClusterModelInput) => Promise<ManagementResult>;
  onAccepted?: () => void;
};
type Review = { revision: number } & (
  | { kind: "update"; input: UpdateClusterModelInput }
  | { kind: "delete"; input: DeprovisionClusterModelInput }
);
export function SharedModelManagementPanel(props: SharedModelManagementPanelProps) {
  return (
    <ManagementPanel
      key={`${props.model.organizationId}:${props.model.id}:${props.model.clusterId}:${props.model.providerId}`}
      {...props}
    />
  );
}
function ManagementPanel(props: SharedModelManagementPanelProps) {
  const { model, draft, onDraftChange, admission } = props;
  const t = useTranslations("models.shared.management"),
    placement = useTranslations("models.shared.placement");
  const inventory = useTranslations("models.shared.inventory");
  const id = useId();
  const access = props.access ?? modelAccessDraft(model);
  const request = modelSettingsRequest(model, draft, access),
    requestKey = JSON.stringify(request);
  const idle = model.status === "active" || model.status === "failed";
  const manageable =
    props.canManage && !props.capabilityLoading && !props.capabilityError && !props.blocked && idle;
  const updatable =
    manageable &&
    !!request &&
    admission?.requestKey === requestKey &&
    admission.eligible &&
    !props.admissionLoading &&
    !props.admissionError;
  const scopeKey = JSON.stringify([
    model.organizationId,
    model.id,
    model.version,
    model.clusterId,
    model.providerId,
    model.status,
    manageable,
  ]);
  const updateScopeKey = JSON.stringify([
    scopeKey,
    requestKey,
    admission,
    props.admissionLoading,
    props.admissionError,
  ]);
  const [scope, setScope] = useState({
    key: scopeKey,
    revision: 0,
    updateKey: updateScopeKey,
    updateRevision: 0,
  });
  if (scope.key !== scopeKey || scope.updateKey !== updateScopeKey)
    setScope({
      key: scopeKey,
      revision: scope.revision + Number(scope.key !== scopeKey),
      updateKey: updateScopeKey,
      updateRevision: scope.updateRevision + Number(scope.updateKey !== updateScopeKey),
    });
  const [review, setReview] = useState<Review | null>(null),
    [failure, setFailure] = useState<string | null>(null),
    [accepted, setAccepted] = useState<{ kind: "update" | "delete"; operationId: string } | null>(
      null
    );
  const observedOperation =
    !!accepted &&
    accepted.operationId === model.operationId &&
    !!model.operationCompletedAt &&
    Number.isFinite(Date.parse(model.operationCompletedAt));
  const awaitingOperation = !!accepted && !observedOperation;
  const mounted = useRef(true),
    lifecycle = useRef(0),
    latest = useRef({ revision: scope.revision, updateRevision: scope.updateRevision, props });
  useLayoutEffect(() => {
    latest.current = { revision: scope.revision, updateRevision: scope.updateRevision, props };
  });
  useLayoutEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      lifecycle.current += 1;
    };
  }, []);
  const reviewCurrent =
    !!review &&
    review.revision === (review.kind === "update" ? scope.updateRevision : scope.revision) &&
    manageable &&
    (review.kind === "delete" || updatable);
  async function confirm() {
    if (!review || !reviewCurrent) {
      setFailure(t("changed"));
      return false;
    }
    const candidate = review,
      epoch = lifecycle.current;
    try {
      setFailure(null);
      const result =
        candidate.kind === "update"
          ? await props.onUpdate(candidate.input)
          : await props.onDelete(candidate.input);
      if (
        !mounted.current ||
        lifecycle.current !== epoch ||
        (candidate.kind === "update" ? latest.current.updateRevision : latest.current.revision) !==
          candidate.revision
      )
        return false;
      if (!result.accepted) {
        setFailure(result.message);
        return false;
      }
      if (!result.operationId) {
        setFailure(t("failed"));
        return false;
      }
      setAccepted({ kind: candidate.kind, operationId: result.operationId });
      try {
        latest.current.props.onAccepted?.();
      } catch {
        /* A read failure cannot undo the accepted operation. */
      }
      return true;
    } catch {
      if (
        mounted.current &&
        lifecycle.current === epoch &&
        (candidate.kind === "update" ? latest.current.updateRevision : latest.current.revision) ===
          candidate.revision
      )
        setFailure(t("failed"));
      return false;
    }
  }
  return (
    <section id="model-settings" aria-labelledby={`${id}-title`} className="scroll-mt-20 space-y-4">
      <h2 id={`${id}-title`} className="text-lg font-semibold">
        {inventory("settings")}
      </h2>
      <p className="text-muted-foreground text-sm">{inventory("settingsDescription")}</p>
      <p className="text-muted-foreground text-sm">{inventory("immutableSource")}</p>
      {props.capabilityLoading ? (
        <p role="status">{t("checking")}</p>
      ) : props.capabilityError ? (
        <div role="alert">
          <p>{props.capabilityError}</p>
          <Button type="button" variant="outline" onClick={props.onRetryCapabilities}>
            {placement("retry")}
          </Button>
        </div>
      ) : !props.canManage ? (
        <p role="status">{t("readOnly")}</p>
      ) : !idle || props.blocked ? (
        <p role="status">{t("blocked")}</p>
      ) : null}
      <fieldset className="space-y-3 sm:col-span-2" disabled={!manageable}>
        <legend className="font-medium">{inventory("access")}</legend>
        <p className="text-muted-foreground text-sm">{inventory("sharingHelp")}</p>
        <div className="flex gap-4">
          {(["SHARED", "DEDICATED"] as const).map((mode) => (
            <Label key={mode} className="flex gap-2">
              <input
                type="radio"
                name={`${id}-sharing`}
                checked={access.mode === mode}
                disabled={!manageable || !props.onAccessChange}
                onChange={() => props.onAccessChange?.(mode)}
              />
              {inventory(mode === "SHARED" ? "shared" : "dedicated")}
            </Label>
          ))}
        </div>
        {access.mode === "DEDICATED" && (
          <div className="space-y-3">
            <p>
              {access.app
                ? inventory("dedicatedApp", { app: access.app.name })
                : inventory("chooseDedicatedApp")}
            </p>
            {props.dedicatedApps && (
              <ListPage
                {...props.dedicatedApps}
                embedded
                label={inventory("selectApp")}
                getRowId={(app) => app.id}
                columns={[
                  {
                    id: "app",
                    header: inventory("selectApp"),
                    cell: (app) => (
                      <Button
                        type="button"
                        variant="outline"
                        disabled={
                          !manageable ||
                          props.dedicatedApps?.loading ||
                          props.dedicatedApps?.stale ||
                          !!props.dedicatedApps?.error ||
                          !props.onSelectDedicatedApp
                        }
                        onClick={() => props.onSelectDedicatedApp?.(app)}
                      >
                        {app.name} · {app.slug}
                      </Button>
                    ),
                  },
                ]}
                empty={{
                  icon: <UsersIcon />,
                  title: inventory("appsEmpty"),
                  description: inventory("appsEmptyHelp"),
                }}
              />
            )}
          </div>
        )}
      </fieldset>
      <form
        className="grid gap-4 sm:grid-cols-2"
        onSubmit={(event) => {
          event.preventDefault();
          if (!updatable || !request) return;
          setFailure(null);
          setAccepted(null);
          setReview({
            kind: "update",
            revision: scope.updateRevision,
            input: request,
          });
        }}
      >
        <div className="space-y-2 sm:col-span-2">
          <Label htmlFor={`${id}-name`}>{placement("name")}</Label>
          <Input
            id={`${id}-name`}
            value={draft.name}
            disabled={!manageable}
            required
            maxLength={128}
            onChange={(event) => onDraftChange("name", event.target.value)}
          />
        </div>

        {(
          [
            "cpuRequest",
            "memoryRequest",
            "gpuCount",
            ...(model.computeMode === "cpu" ? (["cpuKvCacheGiB"] as const) : []),
          ] as const
        ).map((field) => (
          <div key={field} className="space-y-2">
            <Label htmlFor={`${id}-${field}`}>{placement(field)}</Label>
            <Input
              id={`${id}-${field}`}
              value={draft[field]}
              disabled={!manageable}
              type={field === "gpuCount" || field === "cpuKvCacheGiB" ? "number" : "text"}
              min={field === "cpuKvCacheGiB" ? 1 : 0}
              step={1}
              required={field !== "cpuKvCacheGiB"}
              onChange={(event) => onDraftChange(field, event.target.value)}
            />
            <p className="text-muted-foreground text-xs">{placement(`${field}Help`)}</p>
          </div>
        ))}
        <Label className="flex items-center gap-2 sm:col-span-2">
          <input
            type="checkbox"
            checked={draft.allowSubscriptions}
            disabled={!manageable}
            onChange={(event) => onDraftChange("allowSubscriptions", event.target.checked)}
          />
          {placement("allowSubscriptions")}
        </Label>
        <p className="text-muted-foreground text-sm sm:col-span-2">{t("subscriptionPolicy")}</p>
        <p className="border-warning-border bg-warning-bg text-warning-fg rounded-md border p-3 text-sm sm:col-span-2">
          {t("restartImpact")}
        </p>
        {props.canManage && (
          <div className="space-y-2 sm:col-span-2">
            {props.admissionLoading ? (
              <p role="status">{placement("verifying")}</p>
            ) : props.admissionError ? (
              <div role="alert">
                <p>{props.admissionError}</p>
                <Button type="button" variant="outline" onClick={props.onRetryAdmission}>
                  {placement("retry")}
                </Button>
              </div>
            ) : !admission?.eligible ? (
              <p>{admission?.reason ?? placement("unverified")}</p>
            ) : (
              <p className="text-muted-foreground text-sm">{placement("hardwareUnknown")}</p>
            )}
          </div>
        )}
        <Button type="submit" disabled={!updatable || awaitingOperation}>
          {t("reviewUpdate")}
        </Button>
      </form>
      <div className="space-y-2 border-t pt-4">
        <p className="text-muted-foreground text-sm">{t("deleteRetained")}</p>
        <Button
          type="button"
          variant="destructive"
          disabled={!manageable || awaitingOperation}
          onClick={() => {
            setFailure(null);
            setAccepted(null);
            setReview({
              kind: "delete",
              revision: scope.revision,
              input: {
                organizationId: model.organizationId,
                id: model.id,
                expectedClusterId: model.clusterId,
                expectedProviderId: model.providerId,
                ifMatchVersion: model.version,
                deleteData: false,
              },
            });
          }}
        >
          {t("reviewDelete")}
        </Button>
      </div>
      {accepted && (
        <p role="status">
          {t(
            observedOperation && model.status === "failed"
              ? "operationFailed"
              : observedOperation && accepted.kind === "update" && model.status === "active"
                ? "completedUpdate"
                : accepted.kind === "update"
                  ? "acceptedUpdate"
                  : "acceptedDelete"
          )}
        </p>
      )}
      <ConfirmDialog
        open={!!review}
        onOpenChange={(open) => {
          if (!open) {
            setReview(null);
            setFailure(null);
          }
        }}
        title={t(review?.kind === "delete" ? "deleteTitle" : "updateTitle", { name: model.name })}
        confirmLabel={t(review?.kind === "delete" ? "confirmDelete" : "confirmUpdate")}
        destructive={review?.kind === "delete"}
        confirmDisabled={!reviewCurrent}
        onConfirm={confirm}
        description={
          <span className="space-y-3">
            <span className="block break-all">
              {model.name} · {model.clusterName} · {model.modelRepo}
            </span>
            {review?.kind === "update" && (
              <span className="block">
                {placement("name")}: {review.input.name}
                <span className="block">
                  {inventory("access")}:{" "}
                  {inventory(review.input.sharingMode === "DEDICATED" ? "dedicated" : "shared")}
                  {review.input.sharingMode === "DEDICATED" && access.app
                    ? ` · ${access.app.name} · ${access.app.slug}`
                    : ""}
                </span>
              </span>
            )}
            {review?.kind === "update" && (
              <span className="block">
                {placement("reviewResources", {
                  cpu: review.input.cpuRequest,
                  memory: review.input.memoryRequest,
                  gpu: review.input.gpuCount,
                  cache:
                    review.input.cpuKvCacheGiB == null
                      ? placement("notRequested")
                      : `${review.input.cpuKvCacheGiB} GiB`,
                })}
              </span>
            )}
            <span className="block">
              {t(review?.kind === "delete" ? "deleteRetained" : "restartImpact")}
            </span>
            {!reviewCurrent && (
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
      />
    </section>
  );
}
