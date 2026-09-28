"use client";

import { useQuery } from "@apollo/client/react";

import { LIST_CLUSTERS } from "@/graphql/clusters/clusters.queries";
import type { AstroliftTenantCluster } from "@/graphql/clusters/clusters.types";
import { GET_DEPLOYMENT_METRICS } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftDeploymentMetrics } from "@/graphql/lifecycle/lifecycle.types";
import { LIST_ALERT_EVENTS } from "@/graphql/operations/alerts.queries";
import { LIST_AUDIT_EVENTS, LIST_WORKFLOW_RUNS } from "@/graphql/operations/operations.queries";
import type {
  AstroliftAuditEvent,
  AstroliftWorkflowRun,
} from "@/graphql/operations/operations.types";

interface ClustersResp {
  astroliftClusters: AstroliftTenantCluster[];
}
interface MetricsResp {
  astroliftDeploymentMetrics: AstroliftDeploymentMetrics;
}
export interface OpsAlertEvent {
  id: string;
  ruleId: string;
  severity: string;
  firedAt: string;
  resolvedAt: string | null;
  acknowledgedAt: string | null;
  summary: string;
  detail: Record<string, unknown>;
}
interface AlertsResp {
  astroliftAlertEvents: OpsAlertEvent[];
}
interface AuditResp {
  astroliftAuditEvents: AstroliftAuditEvent[];
}
interface RunsResp {
  astroliftWorkflowRuns: AstroliftWorkflowRun[];
}

/**
 * The live operator view: clusters, the last 24h of rollouts, unresolved
 * alerts, recent workflow runs, and the recent audit trail.
 */
export function useOps() {
  const clusters = useQuery<ClustersResp>(LIST_CLUSTERS, {
    pollInterval: 60000,
  });
  const metrics = useQuery<MetricsResp>(GET_DEPLOYMENT_METRICS, {
    variables: { windowDays: 1 },
    pollInterval: 30000,
  });
  const alerts = useQuery<AlertsResp>(LIST_ALERT_EVENTS, {
    variables: { unresolvedOnly: true, limit: 10 },
    pollInterval: 30000,
  });
  const audit = useQuery<AuditResp>(LIST_AUDIT_EVENTS, {
    variables: { limit: 10 },
    pollInterval: 30000,
  });
  const runs = useQuery<RunsResp>(LIST_WORKFLOW_RUNS, {
    variables: { limit: 5 },
    pollInterval: 30000,
  });

  return {
    clusters: clusters.data?.astroliftClusters ?? [],
    clustersLoading: clusters.loading,
    metrics: metrics.data?.astroliftDeploymentMetrics,
    metricsLoading: metrics.loading,
    alerts: alerts.data?.astroliftAlertEvents ?? [],
    alertsLoading: alerts.loading,
    audit: audit.data?.astroliftAuditEvents ?? [],
    auditLoading: audit.loading,
    runs: runs.data?.astroliftWorkflowRuns ?? [],
    runsLoading: runs.loading,
  };
}
