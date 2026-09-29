"use client";

import { useQuery } from "@apollo/client/react";

import { GET_WORKLOAD_MANIFEST } from "@/graphql/registry/registry.queries";

export interface K8sResource {
  apiVersion: string;
  kind: string;
  metadata: { name: string; [k: string]: unknown };
  [k: string]: unknown;
}

export interface WorkloadManifest {
  appSlug: string;
  workloadSlug: string;
  environmentName: string;
  imageTag: string;
  namespace: string;
  resources: K8sResource[];
  previousImageTag: string;
  previousDeploymentId: string;
  resourcesPrevious: K8sResource[];
  error: string | null;
  errorPath: string | null;
  errorLine: number | null;
  errorColumn: number | null;
}

interface ManifestResp {
  astroliftWorkloadManifest: WorkloadManifest | null;
}

/** The rendered Kubernetes manifest for one workload. The data half of ManifestCardView. */
export function useWorkloadManifest(
  appSlug: string,
  workloadSlug: string,
  environmentName: string | null
) {
  const { data, loading } = useQuery<ManifestResp>(GET_WORKLOAD_MANIFEST, {
    variables: { appSlug, workloadSlug, environmentName, imageTag: null },
    fetchPolicy: "cache-and-network",
  });
  return { manifest: data?.astroliftWorkloadManifest ?? null, loading };
}
