"use client";

import { useMutation } from "@apollo/client/react";
import * as React from "react";
import { toast } from "sonner";
import { useTranslations } from "next-intl";
import { refetchAfterMutation } from "@/lib/apollo/mutation-feedback";

import { DEPLOY_CLUSTER_AGENT, ISSUE_CLUSTER_AGENT_KEY } from "@/graphql/clusters/clusters.queries";
import type { MutationResult } from "@/graphql/identity/identity.types";
import type { AgentKeyIssuedData, ClusterWithHeartbeat } from "./types";

interface AgentDeployedData {
  id: string;
  slug: string;
  agentProvisioned: boolean;
  heartbeatStatus: string;
}

/** The scoped heartbeat key is returned once; only keep it for the current cluster. */
export function useClusterAgent(cluster: ClusterWithHeartbeat) {
  const t = useTranslations("clusterSettings.agent");
  const [issue, { loading: issuing }] = useMutation<{
    issueClusterAgentKey: MutationResult<AgentKeyIssuedData>;
  }>(ISSUE_CLUSTER_AGENT_KEY, {
    refetchQueries: (result) => (result.data?.issueClusterAgentKey.ok ? ["GetCluster"] : []),
    onQueryUpdated: (query) => refetchAfterMutation(query, t("feedbackRefreshWarning")),
    awaitRefetchQueries: true,
  });
  const [deploy, { loading: deploying }] = useMutation<{
    deployClusterAgent: MutationResult<AgentDeployedData>;
  }>(DEPLOY_CLUSTER_AGENT, {
    refetchQueries: (result) => (result.data?.deployClusterAgent.ok ? ["GetCluster"] : []),
    onQueryUpdated: (query) => refetchAfterMutation(query, t("feedbackRefreshWarning")),
    awaitRefetchQueries: true,
  });
  const [keyState, setKeyState] = React.useState<{
    clusterId: string;
    epoch: number;
    issued: AgentKeyIssuedData | null;
  }>({ clusterId: cluster.id, epoch: 0, issued: null });
  if (keyState.clusterId !== cluster.id) {
    setKeyState({ clusterId: cluster.id, epoch: keyState.epoch + 1, issued: null });
  }
  const issued = keyState.clusterId === cluster.id ? keyState.issued : null;

  async function onIssue() {
    const requestedId = cluster.id;
    const requestedEpoch = keyState.epoch;
    try {
      const { data } = await issue({ variables: { input: { clusterId: requestedId } } });
      const result = data?.issueClusterAgentKey;
      if (result?.ok && result.data?.clusterId === requestedId) {
        const issued = result.data;
        setKeyState((previous) =>
          previous.clusterId === requestedId && previous.epoch === requestedEpoch
            ? { ...previous, issued }
            : previous
        );
        toast.success(t(issued.rotated ? "feedbackRotated" : "feedbackIssued"));
      } else {
        toast.error(result?.errors?.[0]?.message ?? t("feedbackIssueFailed"));
      }
    } catch (error) {
      toast.error(
        error instanceof Error && error.message ? error.message : t("feedbackIssueFailed")
      );
    }
  }

  async function onDeploy() {
    try {
      const { data } = await deploy({ variables: { input: { clusterId: cluster.id } } });
      const result = data?.deployClusterAgent;
      if (result?.ok) {
        toast.success(t("feedbackDeployed"));
      } else {
        toast.error(result?.errors?.[0]?.message ?? t("feedbackDeployFailed"));
      }
    } catch (error) {
      toast.error(
        error instanceof Error && error.message ? error.message : t("feedbackDeployFailed")
      );
    }
  }

  return {
    clusterId: cluster.id,
    provisioned: cluster.agentProvisioned ?? false,
    heartbeatIntervalSeconds: cluster.heartbeatIntervalSeconds ?? 30,
    issued,
    issuing,
    deploying,
    onIssue,
    onDeploy,
    onDismissIssued: () => setKeyState((previous) => ({ ...previous, issued: null })),
  };
}
