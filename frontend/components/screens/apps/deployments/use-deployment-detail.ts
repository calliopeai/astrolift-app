"use client";

import { useQuery } from "@apollo/client/react";

import { GET_DEPLOYMENT_LOG, LIST_ENVIRONMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import type {
  AstroliftAppEnvironment,
  AstroliftDeployment,
  AstroliftDeploymentLogEntry,
} from "@/graphql/lifecycle/lifecycle.types";
import { GET_RENDERED_MANIFEST } from "@/graphql/registry/registry.queries";

interface EnvsResp {
  astroliftEnvironments: AstroliftAppEnvironment[];
}
interface LogResp {
  astroliftDeploymentLog: AstroliftDeploymentLogEntry[];
}
export interface RenderedManifest {
  appSlug: string;
  environmentName?: string | null;
  imageTag?: string | null;
  namespace: string;
  resources: unknown;
  error?: string | null;
  errorPath?: string | null;
  errorLine?: number | null;
  errorColumn?: number | null;
}
interface ManifestResp {
  astroliftRenderedManifest: RenderedManifest | null;
}

/**
 * The expanded row's data: its deployment log, the rendered manifest and
 * the environment's live URL. The data half of DeploymentExpandPanelView;
 * mounted only while the row is open.
 */
export function useDeploymentDetail(deployment: AstroliftDeployment) {
  const d = deployment;
  const log = useQuery<LogResp>(GET_DEPLOYMENT_LOG, {
    variables: { deploymentId: d.id },
    fetchPolicy: "cache-and-network",
  });
  const manifest = useQuery<ManifestResp>(GET_RENDERED_MANIFEST, {
    variables: {
      appSlug: d.registeredAppSlug,
      environmentName: d.environmentName,
      imageTag: d.imageTag || null,
    },
    fetchPolicy: "cache-first",
  });
  const env = useQuery<EnvsResp>(LIST_ENVIRONMENTS, {
    variables: { appSlug: d.registeredAppSlug },
    fetchPolicy: "cache-first",
  });
  const envObj = env.data?.astroliftEnvironments?.find((e) => e.name === d.environmentName);

  return {
    logEntries: log.data?.astroliftDeploymentLog ?? [],
    logLoading: log.loading,
    manifest: manifest.data?.astroliftRenderedManifest ?? null,
    manifestLoading: manifest.loading,
    /** The environment's public URL, when it has one. */
    envUrl: envObj?.url ?? null,
  };
}
