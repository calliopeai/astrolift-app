"use client";

import { useMutation, useQuery, useSubscription } from "@apollo/client/react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import { refetchAfterMutation } from "@/lib/apollo/mutation-feedback";

import type { DeploymentByIdInput } from "@/graphql/__generated__/schema";
import {
  ABORT_DEPLOYMENT,
  APPROVE_DEPLOYMENT,
  DELETE_DEPLOYMENT,
  REDEPLOY_APP,
  ROLLBACK_DEPLOYMENT,
} from "@/graphql/lifecycle/lifecycle.mutations";
import {
  GET_DEPLOYMENT,
  GET_DEPLOYMENT_APPROVAL_HISTORY,
  GET_DEPLOYMENT_LOG,
  GET_DEPLOYMENT_RELEASE_NOTES,
} from "@/graphql/lifecycle/lifecycle.queries";
import { DEPLOYMENT_LIFECYCLE_STREAM } from "@/graphql/lifecycle/lifecycle.subscriptions";
import type {
  AstroliftDeployment,
  AstroliftDeploymentLogEntry,
  AstroliftReleaseNotes,
} from "@/graphql/lifecycle/lifecycle.types";
import { LIST_EVENTS } from "@/graphql/operations/operations.queries";
import { GET_RENDERED_MANIFEST } from "@/graphql/registry/registry.queries";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

import { IN_FLIGHT } from "./deployments-format";
import { deploymentLogLines, deploySha } from "./deployments-list";
import { downloadText, logText, useNow } from "./run-support";

interface MutationResultLite<T> {
  ok: boolean;
  errors: { code: string; message: string }[];
  data: T | null;
}

interface DeploymentResp {
  astroliftDeployment: AstroliftDeployment | null;
}

interface LogResp {
  astroliftDeploymentLog: AstroliftDeploymentLogEntry[];
}

export interface DeploymentRenderedManifest {
  appSlug: string;
  environmentName?: string | null;
  imageTag?: string | null;
  namespace: string;
  resources: Record<string, unknown>;
  error?: string | null;
  errorPath?: string | null;
  errorLine?: number | null;
  errorColumn?: number | null;
}

interface ManifestResp {
  astroliftRenderedManifest: DeploymentRenderedManifest | null;
}

export interface DeploymentEvent {
  id: string;
  eventType: string;
  payload: Record<string, unknown>;
  registeredAppId?: string | null;
  occurredAt: string;
}

interface EventsResp {
  astroliftEvents: DeploymentEvent[];
}

export interface ApprovalHistoryEntry {
  id: string;
  action: string;
  decision: string;
  actorKind: string;
  actorId: string;
  actorDisplay: string;
  occurredAt: string;
  reason: string;
}

function reportResult(
  label: string,
  result: MutationResultLite<AstroliftDeployment> | null | undefined
) {
  if (!result) throw new Error(`${label} failed`);
  if (result.ok) {
    toast.success(`${label}: ${result.data?.status ?? "ok"}`);
  } else {
    throw new Error(result.errors[0]?.message ?? `${label} failed`);
  }
}

/**
 * One deployment's detail page data: the row, its lifecycle log, the
 * rendered manifest, recent events, the approval trail, release notes,
 * live push and the lifecycle mutations. The data half of
 * DeploymentDetailScreen. Confirmed actions throw so their dialog stays
 * open with the reason; direct redeploy actions surface failures in a toast.
 */
export function useDeploymentDetail(id: string) {
  const t = useTranslations("lists.deploymentDetail");
  const { can } = useMyPermissions();
  const router = useRouter();

  const {
    data: dData,
    loading: dLoading,
    error: dError,
    refetch: refetchDeployment,
  } = useQuery<DeploymentResp>(GET_DEPLOYMENT, { variables: { id } });
  const {
    data: lData,
    loading: lLoading,
    error: lError,
    refetch: refetchLog,
  } = useQuery<LogResp>(GET_DEPLOYMENT_LOG, {
    variables: { deploymentId: id },
  });

  const deployment = dData?.astroliftDeployment ?? null;
  const manifest = useQuery<ManifestResp>(GET_RENDERED_MANIFEST, {
    variables: {
      appSlug: deployment?.registeredAppSlug ?? "",
      environmentName: deployment?.environmentName ?? null,
      imageTag: deployment?.imageTag ?? null,
    },
    skip: !deployment,
    fetchPolicy: "cache-first",
  });

  const events = useQuery<EventsResp>(LIST_EVENTS, {
    variables: { appSlug: deployment?.registeredAppSlug ?? null, limit: 50 },
    skip: !deployment,
    fetchPolicy: "cache-and-network",
  });

  // #653 approval / review trail. Fetched lazily — only deploys that
  // gated through quorum produce history rows, so the resolver returns
  // [] for the trivial "no approvals required" case.
  const approvalHistory = useQuery<{
    astroliftDeploymentApprovalHistory: ApprovalHistoryEntry[];
  }>(GET_DEPLOYMENT_APPROVAL_HISTORY, {
    variables: { deploymentId: id },
    skip: !deployment,
    fetchPolicy: "cache-and-network",
  });

  // #738 — release notes: diff between prev-successful deploy SHA and
  // this deploy's SHA. Fetched lazily after the deployment row lands.
  const releaseNotesQuery = useQuery<{
    astroliftDeploymentReleaseNotes: AstroliftReleaseNotes | null;
  }>(GET_DEPLOYMENT_RELEASE_NOTES, {
    variables: { deploymentId: id },
    skip: !deployment,
    fetchPolicy: "cache-and-network",
  });

  // Live push: any lifecycle event triggers both refetches when the
  // event is for this deployment. Unrelated org events don't churn
  // the page. The subscription payload is loosely typed in Apollo's
  // generic; we narrow at the use-site.
  useSubscription(DEPLOYMENT_LIFECYCLE_STREAM, {
    onData: ({ data: payload }) => {
      const stream = payload.data as
        | { astroliftDeploymentLifecycleStream?: { deploymentId?: string } }
        | undefined;
      const evt = stream?.astroliftDeploymentLifecycleStream;
      if (evt && evt.deploymentId === id) {
        refetchDeployment().catch(() => {});
        refetchLog().catch(() => {});
      }
    },
  });

  const refetch = [
    { query: GET_DEPLOYMENT, variables: { id } },
    { query: GET_DEPLOYMENT_LOG, variables: { deploymentId: id } },
  ];
  const [approve, approveState] = useMutation<{
    approveDeployment: MutationResultLite<AstroliftDeployment>;
  }>(APPROVE_DEPLOYMENT, { onQueryUpdated: refetchAfterMutation, refetchQueries: refetch });
  const [abort, abortState] = useMutation<{
    abortDeployment: MutationResultLite<AstroliftDeployment>;
  }>(ABORT_DEPLOYMENT, { onQueryUpdated: refetchAfterMutation, refetchQueries: refetch });
  const [rollback, rollbackState] = useMutation<{
    rollbackDeployment: MutationResultLite<AstroliftDeployment>;
  }>(ROLLBACK_DEPLOYMENT, { onQueryUpdated: refetchAfterMutation, refetchQueries: refetch });
  const [redeploy, redeployState] = useMutation<
    { redeployApp: MutationResultLite<AstroliftDeployment> },
    { input: DeploymentByIdInput }
  >(REDEPLOY_APP, { onQueryUpdated: refetchAfterMutation, refetchQueries: refetch });
  const [deleteDeployment, deleteState] = useMutation<{
    deleteDeployment: MutationResultLite<Pick<AstroliftDeployment, "id" | "status">>;
  }>(DELETE_DEPLOYMENT);

  const busy =
    approveState.loading ||
    abortState.loading ||
    rollbackState.loading ||
    redeployState.loading ||
    deleteState.loading;

  const log = React.useMemo(() => lData?.astroliftDeploymentLog ?? [], [lData]);

  // The run page's clock ticks while the rollout is in flight (spec 44 §5.5).
  const live = Boolean(deployment && IN_FLIGHT.has(deployment.status));
  const now = useNow(live);

  function onDownload() {
    if (!deployment) return;
    downloadText(
      `deploy-${deployment.registeredAppSlug}-${deploySha(deployment)}.log`,
      logText(deploymentLogLines(log))
    );
  }

  async function onApprove() {
    if (!deployment) return;
    try {
      const { data } = await approve({
        variables: { input: { id: deployment.id } },
      });
      reportResult("approveDeployment", data?.approveDeployment);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "approveDeployment failed");
    }
  }

  async function onAbort(reason: string) {
    if (!deployment) return;
    const { data } = await abort({
      variables: { input: { id: deployment.id, reason } },
    });
    reportResult("abortDeployment", data?.abortDeployment);
  }

  async function onRollback() {
    if (!deployment) return;
    const { data } = await rollback({ variables: { input: { id: deployment.id } } });
    reportResult("rollbackDeployment", data?.rollbackDeployment);
  }

  async function onRedeploy() {
    if (!deployment) return;
    try {
      const { data } = await redeploy({
        variables: { input: { id: deployment.id } },
      });
      if (!data?.redeployApp?.ok) {
        throw new Error(data?.redeployApp?.errors[0]?.message || t("redeployFailed"));
      }
      reportResult(t("redeploy"), data.redeployApp);
    } catch (error) {
      toast.error(error instanceof Error && error.message ? error.message : t("redeployFailed"));
    }
  }

  async function onDelete() {
    if (!deployment) return;
    const { data } = await deleteDeployment({ variables: { input: { id: deployment.id } } });
    const result = data?.deleteDeployment;
    if (result?.ok) {
      // Running rows are superseded (stay in history); terminal
      // rows are soft-deleted. Either way the operator is done
      // here, so bounce back to the list.
      toast.success(t("confirmDelete.success"));
      router.push("/deployments");
    } else if (result) {
      throw new Error(result.errors[0]?.message ?? t("confirmDelete.failed"));
    }
  }

  return {
    deployment,
    loading: dLoading,
    error: dError && !dData ? { message: dError.message } : null,
    onRetry: () => {
      void refetchDeployment();
    },
    now,
    log,
    logLoading: lLoading,
    logError: lError && !lData ? { message: lError.message } : null,
    onRetryLog: () => {
      void refetchLog();
    },
    onDownload,
    manifest: manifest.data?.astroliftRenderedManifest ?? null,
    manifestLoading: manifest.loading,
    events: events.data?.astroliftEvents ?? [],
    eventsLoading: events.loading && !events.data,
    approvalHistory: approvalHistory.data?.astroliftDeploymentApprovalHistory ?? [],
    approvalHistoryLoading: approvalHistory.loading && !approvalHistory.data,
    releaseNotes: releaseNotesQuery.data?.astroliftDeploymentReleaseNotes ?? null,
    canApprove: can("app.approve_deploy"),
    canDeploy: can("app.deploy"),
    canRollback: can("app.rollback"),
    busy,
    onApprove,
    onAbort,
    onRollback,
    onRedeploy,
    onDelete,
  };
}
