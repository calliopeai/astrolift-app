"use client";

import { ModelHostingJourney } from "./ModelHostingJourney";

import { useLayoutEffect, useRef, useState, useId, type ReactNode } from "react";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { ServerIcon } from "lucide-react";
import { PageShell } from "@/components/PageShell";
import { ListPage } from "@/components/list/ListPage";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { ModelPage } from "./ModelSubscriptionsPanel";
import {
  sharedModelRequest,
  type SharedModelDraft,
  type SharedModelSource,
  type SharedModelRequest,
} from "./shared-model-form";

export type ModelPlacementCluster = {
  id: string;
  providerId: string;
  slug: string;
  name: string;
  active: boolean;
  reason: string | null;
};
export type ModelPlacementAdmission = {
  requestKey: string;
  eligible: boolean;
  reason: string | null;
  runtimeVersion: string | null;
  architecture: string | null;
  hardwareAdmission: string;
};
export interface SharedModelDeploymentScreenProps {
  initialStep?: 0 | 1 | 2;
  runtimeSetup?: ReactNode;
  organizationId: string;
  catalogue: ReactNode;
  sourceControls: ReactNode;
  sourceConnection: { connectionId: string; expectedConnectionVersion: number } | null;
  sourceAccess: {
    confirmed: boolean;
    loading: boolean;
    reason: string | null;
    license: string | null;
    onRetry: () => void;
  };
  hostingAllowed: boolean;
  licenseReviewed: boolean;
  onLicenseReviewed: (reviewed: boolean) => void;
  model: SharedModelSource | null;
  onClearModel: () => void;
  clusters: ModelPage<ModelPlacementCluster>;
  selectedClusterId: string | null;
  onSelectCluster: (id: string) => void;
  draft: SharedModelDraft;
  onDraftChange: <K extends keyof SharedModelDraft>(field: K, value: SharedModelDraft[K]) => void;
  admission: ModelPlacementAdmission | null;
  admissionLoading: boolean;
  admissionError: string | null;
  onRetryAdmission: () => void;
  onDeploy: (
    request: SharedModelRequest
  ) => Promise<{ accepted: true; deploymentId: string } | { accepted: false; message: string }>;
}

export function SharedModelDeploymentScreen(props: SharedModelDeploymentScreenProps) {
  return <DeploymentScreen key={props.organizationId} {...props} />;
}
function DeploymentScreen(props: SharedModelDeploymentScreenProps) {
  const {
    organizationId,
    catalogue,
    model,
    onClearModel,
    clusters,
    selectedClusterId,
    onSelectCluster,
    draft,
    onDraftChange,
    admission,
    admissionLoading,
    admissionError,
    onRetryAdmission,
  } = props;
  const t = useTranslations("models.shared.placement");
  const restart = useTranslations("models.shared.subscriptions");
  const hosting = useTranslations("models.shared.hosting");
  const usability = useTranslations("models.shared.usability");
  const local = useTranslations("models.shared.localImport");
  const runtimeCopy = useTranslations("models.shared.runtimeSetup");
  const id = useId();
  const modelKey = JSON.stringify(model);
  const [navigation, setNavigation] = useState({
    modelKey,
    step: props.initialStep ?? (model ? 1 : 0),
  });
  if (navigation.modelKey !== modelKey) setNavigation({ modelKey, step: model ? 1 : 0 });
  const step = model ? navigation.step : 0;
  const focusTarget = useRef<string | null>(null);
  const navigate = (next: 0 | 1 | 2, target?: string) => {
    setNavigation({ modelKey, step: next });
    focusTarget.current =
      target ??
      (next === 0
        ? "model-hosting-source"
        : next === 1
          ? "model-hosting-cluster"
          : "model-hosting-checks");
  };
  useLayoutEffect(() => {
    if (!focusTarget.current) return;
    const node = document.getElementById(focusTarget.current);
    if (node instanceof HTMLElement) {
      node.focus();
      node.scrollIntoView?.({ block: "start" });
    }
    focusTarget.current = null;
  });
  const cluster = clusters.rows.find((item) => item.id === selectedClusterId);
  const request = sharedModelRequest(
    organizationId,
    cluster?.active ? cluster : null,
    model,
    draft,
    props.sourceConnection
  );
  const missingFields = [
    ...(!draft.name.trim() ? [{ key: "enterName", target: `${id}-name` }] : []),
    ...(!cluster ? [{ key: "selectCluster", target: "model-hosting-cluster" }] : []),
    ...(!draft.computeMode ? [{ key: "selectCompute", target: `${id}-cpu` }] : []),
    ...(!draft.cpuRequest.trim() ? [{ key: "enterCpu", target: `${id}-cpuRequest` }] : []),
    ...(!draft.memoryRequest.trim() ? [{ key: "enterMemory", target: `${id}-memoryRequest` }] : []),
    ...(!/^\d+$/.test(draft.gpuCount) ||
    (draft.computeMode === "cpu"
      ? Number(draft.gpuCount) !== 0
      : draft.computeMode === "gpu" && Number(draft.gpuCount) < 1)
      ? [{ key: "enterGpu", target: `${id}-gpuCount` }]
      : []),
    ...(draft.computeMode === "cpu" &&
    (!/^[1-9]\d*$/.test(draft.cpuKvCacheGiB) || Number(draft.cpuKvCacheGiB) > 1024)
      ? [{ key: "enterCache", target: `${id}-cpuKvCacheGiB` }]
      : []),
  ];
  const requestKey = JSON.stringify(request);
  const scopeKey = JSON.stringify([
    requestKey,
    clusters.stale,
    clusters.loading,
    Boolean(clusters.error),
    admission,
    admissionLoading,
    admissionError,
    props.sourceAccess.confirmed,
    props.sourceAccess.loading,
    props.sourceAccess.reason,
    props.hostingAllowed,
    props.licenseReviewed,
  ]);
  const [scope, setScope] = useState({ key: scopeKey, revision: 0 });
  if (scope.key !== scopeKey) setScope({ key: scopeKey, revision: scope.revision + 1 });
  const [review, setReview] = useState<{
    request: SharedModelRequest;
    revision: number;
    clusterName: string;
  } | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [createdId, setCreatedId] = useState<string | null>(null);
  const [acceptedContext, setAcceptedContext] = useState(requestKey);
  if (acceptedContext !== requestKey) {
    setAcceptedContext(requestKey);
    setCreatedId(null);
  }
  const eligible = Boolean(
    request &&
    props.hostingAllowed &&
    props.sourceAccess.confirmed &&
    !props.sourceAccess.loading &&
    props.licenseReviewed &&
    admission?.requestKey === requestKey &&
    admission.eligible &&
    !admissionLoading &&
    !admissionError &&
    !clusters.loading &&
    !clusters.stale &&
    !clusters.error
  );
  const placementComplete = Boolean(
    request && props.hostingAllowed && !clusters.loading && !clusters.stale && !clusters.error
  );
  const reviewCurrent = Boolean(
    review &&
    review.revision === scope.revision &&
    eligible &&
    JSON.stringify(review.request) === requestKey
  );
  const mounted = useRef(true),
    lifecycle = useRef(0),
    latest = useRef({ props, revision: scope.revision });
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
  async function confirm() {
    if (!review || !reviewCurrent) {
      setFailure(t("changed"));
      return false;
    }
    const candidate = review;
    const epoch = lifecycle.current;
    setFailure(null);
    try {
      const result = await latest.current.props.onDeploy(candidate.request);
      if (
        !mounted.current ||
        lifecycle.current !== epoch ||
        latest.current.revision !== candidate.revision
      )
        return false;
      if (!result.accepted) {
        setFailure(result.message);
        return false;
      }
      if (!result.deploymentId) {
        setFailure(t("failed"));
        return false;
      }
      setCreatedId(result.deploymentId);
      return true;
    } catch {
      if (mounted.current && latest.current.revision === candidate.revision)
        setFailure(t("failed"));
      return false;
    }
  }
  return (
    <PageShell
      title={hosting("title")}
      description={t("description")}
      actions={
        <Button variant="outline" asChild>
          <Link href="/models">{t("back")}</Link>
        </Button>
      }
    >
      <div className="@container space-y-8">
        <ModelHostingJourney
          sourceAnchor="model-hosting-source"
          clusterAnchor="model-hosting-cluster"
          reviewAnchor="model-hosting-checks"
          modelSelected={Boolean(model)}
          clusterSelected={Boolean(model && cluster)}
          readyForReview={Boolean(eligible && !createdId)}
          accepted={Boolean(createdId)}
          activeStep={step as 0 | 1 | 2}
          onNavigate={(next) => navigate(next)}
        />
        {model && (
          <p className="text-sm font-medium break-all">
            {"repoId" in model ? model.repoId : model.name}
          </p>
        )}
        {step === 0 && (
          <div id="model-hosting-source" tabIndex={-1} className="scroll-mt-6 space-y-4">
            {props.sourceControls}
            {!model && catalogue}
          </div>
        )}
        {model && (
          <>
            {step === 0 && (
              <div className="bg-surface-1 space-y-2 rounded-md border p-4">
                <p className="font-medium break-all">
                  {"repoId" in model ? model.repoId : model.name}
                </p>
                <p className="font-mono text-xs break-all">
                  {"repoId" in model ? model.revisionSha : model.manifestSha256}
                </p>
                <Button type="button" variant="outline" onClick={onClearModel}>
                  {t("changeModel")}
                </Button>
              </div>
            )}
            {step === 1 && (
              <section aria-labelledby="model-hosting-cluster" className="space-y-3">
                <h2
                  id="model-hosting-cluster"
                  tabIndex={-1}
                  className="scroll-mt-6 text-lg font-semibold"
                >
                  {hosting("placementStep")}
                </h2>
                <ListPage
                  embedded
                  {...clusters}
                  label={t("clusters")}
                  getRowId={(row) => row.id}
                  empty={{ icon: <ServerIcon />, title: t("noClusters") }}
                  columns={[
                    {
                      id: "name",
                      header: t("cluster"),
                      cellClassName: "min-w-56",
                      cell: (row) => (
                        <Button
                          type="button"
                          variant={row.id === selectedClusterId ? "secondary" : "ghost"}
                          className="h-auto text-left break-all whitespace-normal"
                          disabled={!row.active || clusters.stale || clusters.loading}
                          onClick={() => onSelectCluster(row.id)}
                        >
                          {row.name} · {row.slug}
                        </Button>
                      ),
                    },
                    {
                      id: "state",
                      header: t("state"),
                      cellClassName: "min-w-48",
                      cell: (row) =>
                        row.active ? t("admissionRequired") : (row.reason ?? t("inactive")),
                    },
                  ]}
                />
                <p role="status" className="text-sm">
                  {cluster ? t("selectedCluster", { cluster: cluster.name }) : t("chooseCluster")}
                </p>
              </section>
            )}
            {missingFields.length > 0 && (
              <div role="status" className="space-y-2">
                <h3 className="font-medium">{usability("nextStep")}</h3>
                <div className="flex flex-wrap gap-2">
                  {missingFields.map((field) => (
                    <Button
                      key={field.key}
                      type="button"
                      variant="outline"
                      onClick={() => navigate(1, field.target)}
                    >
                      {usability(field.key)}
                    </Button>
                  ))}
                </div>
              </div>
            )}
            {step === 1 && props.runtimeSetup}
            <form
              className="grid gap-4 @lg:grid-cols-2"
              onSubmit={(event) => {
                event.preventDefault();
                if (step < 2) {
                  if (placementComplete && step === 1) navigate(2);
                  return;
                }
                if (eligible && request && cluster) {
                  setFailure(null);
                  setReview({ request, revision: scope.revision, clusterName: cluster.name });
                }
              }}
            >
              <div hidden={step !== 1} className="space-y-2 @lg:col-span-2">
                <Label htmlFor={`${id}-name`}>{t("name")}</Label>
                <Input
                  id={`${id}-name`}
                  value={draft.name}
                  maxLength={128}
                  onChange={(event) => onDraftChange("name", event.target.value)}
                  required
                />
              </div>
              <fieldset
                hidden={step !== 1}
                id="model-hosting-resources"
                className="space-y-2 @lg:col-span-2"
              >
                <legend className="text-sm font-medium">{t("compute")}</legend>
                <div className="flex flex-wrap gap-5">
                  {(["cpu", "gpu"] as const).map((mode) => (
                    <Label key={mode} className="flex items-center gap-2">
                      <input
                        type="radio"
                        id={`${id}-${mode}`}
                        name={`${id}-compute`}
                        value={mode}
                        checked={draft.computeMode === mode}
                        onChange={() => onDraftChange("computeMode", mode)}
                      />
                      {t(mode)}
                    </Label>
                  ))}
                </div>
                <p className="text-muted-foreground text-sm">{t("computeHelp")}</p>
                {(
                  [
                    "cpuRequest",
                    "memoryRequest",
                    "gpuCount",
                    ...(draft.computeMode === "cpu" ? (["cpuKvCacheGiB"] as const) : []),
                  ] as const
                ).map((field) => (
                  <div key={field} className="space-y-2">
                    <Label htmlFor={`${id}-${field}`}>{t(field)}</Label>
                    <Input
                      id={`${id}-${field}`}
                      type={field === "gpuCount" || field === "cpuKvCacheGiB" ? "number" : "text"}
                      min={field === "cpuKvCacheGiB" ? 1 : 0}
                      max={field === "cpuKvCacheGiB" ? 1024 : undefined}
                      step={1}
                      value={draft[field]}
                      onChange={(event) => onDraftChange(field, event.target.value)}
                      required
                      readOnly={field === "gpuCount" && draft.computeMode === "cpu"}
                    />
                    <p className="text-muted-foreground text-xs">
                      {field === "cpuKvCacheGiB" ? usability("cpuCacheHelp") : t(`${field}Help`)}
                    </p>
                  </div>
                ))}
                <div className="space-y-2">
                  <Label htmlFor={`${id}-dtype`}>{runtimeCopy("dtype")}</Label>
                  <select
                    id={`${id}-dtype`}
                    value={draft.dtype ?? ""}
                    onChange={(event) =>
                      onDraftChange("dtype", event.target.value as SharedModelDraft["dtype"])
                    }
                  >
                    <option value="">{runtimeCopy("declaredDefault")}</option>
                    {(["AUTO", "FLOAT16", "BFLOAT16", "FLOAT32"] as const).map((value) => (
                      <option key={value} value={value}>
                        {value.toLowerCase()}
                      </option>
                    ))}
                  </select>
                  {(["maxModelLen", "maxNumSeqs"] as const).map((field) => (
                    <div key={field}>
                      <Label htmlFor={`${id}-${field}`}>{runtimeCopy(field)}</Label>
                      <Input
                        id={`${id}-${field}`}
                        type="number"
                        min={field === "maxModelLen" ? 256 : 1}
                        max={field === "maxModelLen" ? 131072 : 4096}
                        step={1}
                        value={draft[field] ?? ""}
                        onChange={(event) => onDraftChange(field, event.target.value)}
                      />
                    </div>
                  ))}
                  <p className="text-muted-foreground text-xs">{runtimeCopy("servingHelp")}</p>
                </div>
              </fieldset>
              <div hidden={step !== 1} className="space-y-2 @lg:col-span-2">
                <Label className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={draft.allowSubscriptions}
                    onChange={(event) => onDraftChange("allowSubscriptions", event.target.checked)}
                  />
                  {t("allowSubscriptions")}
                </Label>
                {draft.allowSubscriptions && (
                  <p className="border-warning-border bg-warning-bg text-warning-fg rounded-md border p-3 text-sm">
                    {restart("restartImpact")}
                  </p>
                )}
              </div>
              {step === 2 && (
                <div
                  id="model-hosting-checks"
                  tabIndex={-1}
                  className="scroll-mt-6 space-y-2 @lg:col-span-2"
                >
                  <h2 className="text-lg font-semibold">{hosting("checksStep")}</h2>
                  {cluster && (
                    <p id="model-hosting-settings-help" className="text-muted-foreground text-sm">
                      {usability("settingsNewTab")}
                    </p>
                  )}
                  <div className="grid gap-4 @xl:grid-cols-2">
                    <section className="bg-surface-1 space-y-2 rounded-md border p-4">
                      <h3 className="font-medium">{hosting("accessTitle")}</h3>
                      <p role="status">
                        {props.sourceAccess.loading
                          ? hosting("accessChecking")
                          : props.sourceAccess.confirmed
                            ? "repoId" in model
                              ? hosting("accessConfirmed")
                              : local("verified")
                            : (props.sourceAccess.reason ?? hosting("accessUnknown"))}
                      </p>
                      <p className="text-muted-foreground text-sm">
                        {"repoId" in model ? hosting("accessHelp") : local("description")}
                      </p>
                      {"repoId" in model && (
                        <a
                          className="text-primary text-sm underline"
                          href={`https://huggingface.co/${model.repoId}`}
                          target="_blank"
                          rel="noreferrer"
                        >
                          {hosting("openModel")}
                        </a>
                      )}
                      {!props.sourceAccess.confirmed && (
                        <Button
                          type="button"
                          variant="outline"
                          disabled={props.sourceAccess.loading || !props.hostingAllowed}
                          onClick={props.sourceAccess.onRetry}
                        >
                          {hosting("retry")}
                        </Button>
                      )}
                    </section>
                    <section className="bg-surface-1 space-y-2 rounded-md border p-4">
                      <h3 className="font-medium">{hosting("licenseTitle")}</h3>
                      {props.sourceAccess.license && <p>{props.sourceAccess.license}</p>}
                      <p className="text-muted-foreground text-sm">
                        {"repoId" in model ? hosting("licenseHelp") : local("localLicense")}
                      </p>
                      <Label className="flex items-center gap-2">
                        <input
                          type="checkbox"
                          checked={props.licenseReviewed}
                          onChange={(event) => props.onLicenseReviewed(event.target.checked)}
                        />
                        {"repoId" in model ? hosting("licenseReview") : local("localLicenseReview")}
                      </Label>
                    </section>
                    <section className="bg-surface-1 space-y-2 rounded-md border p-4">
                      <h3 className="font-medium">{hosting("hardwareTitle")}</h3>
                      <p className="text-muted-foreground text-sm">{hosting("hardwareHelp")}</p>
                      <p className="text-muted-foreground text-sm">{hosting("hardwarePending")}</p>
                      <a
                        className="text-primary block text-sm underline"
                        href="#model-hosting-resources"
                        onClick={(event) => {
                          event.preventDefault();
                          navigate(1, `${id}-cpuRequest`);
                        }}
                      >
                        {hosting("reviewResourceRequests")}
                      </a>
                      {cluster && (
                        <Link
                          className="text-primary block text-sm underline"
                          href={`/clusters/${encodeURIComponent(cluster.slug)}/settings`}
                          target="_blank"
                          rel="noopener noreferrer"
                          aria-describedby="model-hosting-settings-help"
                        >
                          {hosting("hardwareConfiguration")}
                        </Link>
                      )}
                    </section>
                    <section className="bg-surface-1 space-y-2 rounded-md border p-4">
                      <h3 className="font-medium">{hosting("runtimeTitle")}</h3>
                      <p className="text-muted-foreground text-sm">{hosting("runtimeHelp")}</p>
                      <a
                        className="text-primary text-sm underline"
                        href="https://docs.vllm.ai/en/v0.15.1/models/supported_models/"
                        target="_blank"
                        rel="noreferrer"
                      >
                        {hosting("supportedArchitectures")}
                      </a>
                      {cluster && (
                        <Link
                          className="text-primary block text-sm underline"
                          href={`/clusters/${encodeURIComponent(cluster.slug)}/settings`}
                          target="_blank"
                          rel="noopener noreferrer"
                          aria-describedby="model-hosting-settings-help"
                        >
                          {hosting("clusterConfiguration")}
                        </Link>
                      )}
                      {admissionLoading ? (
                        <p role="status">{t("verifying")}</p>
                      ) : admissionError ? (
                        <div role="alert">
                          <p>{admissionError}</p>
                          <Button type="button" variant="outline" onClick={onRetryAdmission}>
                            {t("retry")}
                          </Button>
                        </div>
                      ) : admission?.requestKey === requestKey && admission.eligible ? (
                        <>
                          <p>
                            {t("runtimeVerified", {
                              version: admission.runtimeVersion ?? t("unknown"),
                              architecture: admission.architecture ?? t("unknown"),
                            })}
                          </p>
                          <p className="text-muted-foreground text-sm">{t("hardwareUnknown")}</p>
                        </>
                      ) : (
                        <p role="status">
                          {admission?.requestKey === requestKey && admission.reason
                            ? admission.reason
                            : t("unverified")}
                        </p>
                      )}
                    </section>
                  </div>
                  <Button type="submit" disabled={!eligible || Boolean(createdId)}>
                    {t("review")}
                  </Button>
                </div>
              )}
              <div className="flex gap-3 @lg:col-span-2">
                {step > 0 && (
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => navigate((step - 1) as 0 | 1)}
                  >
                    {usability("previousStep")}
                  </Button>
                )}
                {step < 2 && (
                  <Button
                    type="button"
                    disabled={
                      !props.hostingAllowed ||
                      (step === 1 &&
                        (!request || clusters.loading || clusters.stale || Boolean(clusters.error)))
                    }
                    onClick={() => navigate((step + 1) as 1 | 2)}
                  >
                    {usability("continueStep")}
                  </Button>
                )}
              </div>
            </form>
            {createdId && (
              <div role="status" className="space-y-3">
                <p>{t("accepted")}</p>
                <Button asChild>
                  <Link href={`/models/shared/${encodeURIComponent(createdId)}`}>
                    {t("openDeployment")}
                  </Link>
                </Button>
              </div>
            )}
          </>
        )}
        <ConfirmDialog
          open={Boolean(review)}
          onOpenChange={(open) => {
            if (!open) {
              setReview(null);
              setFailure(null);
            }
          }}
          title={t("reviewTitle")}
          confirmLabel={t("deploy")}
          confirmDisabled={!reviewCurrent}
          onConfirm={confirm}
          description={
            <span className="space-y-3">
              <span className="block break-all">
                {review &&
                  t("reviewTarget", {
                    model:
                      review.request.modelRepo ??
                      (model && "localArtifactId" in model ? model.name : ""),
                    revision:
                      review.request.revisionSha ??
                      (model && "localArtifactId" in model ? model.manifestSha256 : ""),
                    cluster: review.clusterName,
                    compute: t(review.request.computeMode),
                  })}
              </span>
              {review && <span className="block break-all">{review.request.name}</span>}
              {review && (
                <span className="block">
                  {t("reviewResources", {
                    cpu: review.request.cpuRequest,
                    memory: review.request.memoryRequest,
                    gpu: review.request.gpuCount,
                    cache:
                      review.request.cpuKvCacheGiB === null
                        ? t("notRequested")
                        : `${review.request.cpuKvCacheGiB} GiB`,
                  })}
                </span>
              )}
              {review && (
                <span className="block">
                  {(["dtype", "maxModelLen", "maxNumSeqs"] as const).map((field) => (
                    <span key={field} className="block">
                      {runtimeCopy(field)}:{" "}
                      {review.request[field] === null
                        ? runtimeCopy("declaredDefault")
                        : String(review.request[field]).toLowerCase()}
                    </span>
                  ))}
                </span>
              )}
              <span className="block">{t("hardwareUnknown")}</span>
              {review?.request.allowSubscriptions && (
                <span className="block">{restart("restartImpact")}</span>
              )}
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
      </div>
    </PageShell>
  );
}
