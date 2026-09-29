"use client";

import { useQuery } from "@apollo/client/react";
import * as React from "react";

import { LIST_ENVIRONMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftAppEnvironment } from "@/graphql/lifecycle/lifecycle.types";
import { GET_RENDERED_MANIFEST } from "@/graphql/registry/registry.queries";

export interface RenderedManifest {
  appSlug: string;
  environmentName: string;
  imageTag: string;
  namespace: string;
  resources: K8sResource[];
  error: string | null;
  errorPath: string | null;
  errorLine: number | null;
  errorColumn: number | null;
}

export interface K8sResource {
  apiVersion: string;
  kind: string;
  metadata: { name: string };
}

interface ManifestResp {
  astroliftRenderedManifest: RenderedManifest | null;
}

interface EnvsResp {
  astroliftEnvironments: AstroliftAppEnvironment[];
}

export const PREVIEW_SENTINEL = "__preview__";

/**
 * The environment list and the rendered manifest for the picked environment
 * and image tag. The data half of ManifestPreviewScreen.
 */
export function useManifestPreview(slug: string) {
  const [envName, setEnvName] = React.useState<string>(PREVIEW_SENTINEL);
  const [imageTag, setImageTag] = React.useState<string>("");

  const envs = useQuery<EnvsResp>(LIST_ENVIRONMENTS, {
    variables: { appSlug: slug },
  });

  const { data, loading } = useQuery<ManifestResp>(GET_RENDERED_MANIFEST, {
    variables: {
      appSlug: slug,
      environmentName: envName === PREVIEW_SENTINEL ? null : envName,
      imageTag: imageTag.trim() || null,
    },
  });

  return {
    envName,
    setEnvName,
    imageTag,
    setImageTag,
    environments: (envs.data?.astroliftEnvironments ?? []).map((e) => ({ id: e.id, name: e.name })),
    loading,
    result: data?.astroliftRenderedManifest ?? null,
  };
}
