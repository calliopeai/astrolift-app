"use client";

import { useMutation } from "@apollo/client/react";
import * as React from "react";
import { toast } from "sonner";

import { DEPLOY_CLUSTER_AGENT, ISSUE_CLUSTER_AGENT_KEY } from "@/graphql/clusters/clusters.queries";
import type { MutationResult } from "@/graphql/identity/identity.types";

import type { AgentKeyIssuedData, ClusterWithHeartbeat } from "./types";

interface AgentDeployedData {
  id: string;
  slug: string;
  agentProvisioned: boolean;
  heartbeatStatus: string;
}

/**
 * Issues (or rotates) the scoped key the in-cluster keep-alive agent signs
 * its heartbeat with, and deploys the agent (#808). The raw key comes back
 * EXACTLY ONCE in the mutation response and is held here until dismissed.
 * The data half of ClusterAgentView.
 */
export function useClusterAgent(cluster: ClusterWithHeartbeat) {
  const [issue, { loading: issuing }] = useMutation<{
    issueClusterAgentKey: MutationResult<AgentKeyIssuedData>;
  }>(ISSUE_CLUSTER_AGENT_KEY, {
    // The mutation flips agentProvisioned + may change the interval;
    // refetch so the card's "provisioned" state and the live badge stay
    // consistent without a reload.
    refetchQueries: ["GetCluster"],
  });
  const [deploy, { loading: deploying }] = useMutation<{
    deployClusterAgent: MutationResult<AgentDeployedData>;
  }>(DEPLOY_CLUSTER_AGENT, {
    // The deploy lands the agent Deployment; the cluster starts pulsing
    // shortly after, so refetch to let the live badge flip to Connected.
    refetchQueries: ["GetCluster"],
  });
  const [issued, setIssued] = React.useState<AgentKeyIssuedData | null>(null);

  async function onIssue() {
    const { data } = await issue({
      variables: { input: { clusterId: cluster.id } },
    });
    if (data?.issueClusterAgentKey.ok && data.issueClusterAgentKey.data) {
      setIssued(data.issueClusterAgentKey.data);
      toast.success(
        data.issueClusterAgentKey.data.rotated
          ? "Agent key rotated — the previous key no longer works."
          : "Agent key issued — copy it now, it won't be shown again."
      );
    } else {
      toast.error(data?.issueClusterAgentKey.errors?.[0]?.message ?? "Failed to issue key");
    }
  }

  async function onDeploy() {
    const { data } = await deploy({
      variables: { input: { clusterId: cluster.id } },
    });
    if (data?.deployClusterAgent.ok) {
      toast.success(
        "Agent deployed — the cluster connects within a couple of heartbeat intervals."
      );
    } else {
      toast.error(data?.deployClusterAgent.errors?.[0]?.message ?? "Failed to deploy agent");
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
    onDismissIssued: () => setIssued(null),
  };
}
