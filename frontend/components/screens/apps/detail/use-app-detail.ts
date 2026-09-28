"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import type { MutationResult } from "@/graphql/identity/identity.types";
import { START_DEPLOYMENT } from "@/graphql/lifecycle/lifecycle.mutations";
import { LIST_DEPLOYMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftDeployment } from "@/graphql/lifecycle/lifecycle.types";
import { SOFT_DELETE_APP } from "@/graphql/registry/registry.mutations";
import { GET_APP, LIST_APPS, LIST_WORKLOADS } from "@/graphql/registry/registry.queries";
import type { AstroliftRegisteredApp, AstroliftWorkload } from "@/graphql/registry/registry.types";

interface AppResp {
  astroliftApp: AstroliftRegisteredApp | null;
}
interface WorkloadsResp {
  astroliftWorkloads: AstroliftWorkload[];
}

/**
 * The app behind the overview: the app record (with its drift rollup), its
 * workloads, the latest deployment the header Deploy re-runs, and the header
 * Deploy and Delete mutations. The data half of AppDetailScreen.
 */
export function useAppDetail(slug: string) {
  const router = useRouter();
  const tDetail = useTranslations("apps.detail");

  // The header Deploy action redeploys the LATEST deployment (same tag,
  // same env) behind a confirm. It used to be a bare link to the
  // environments page — a rocket-labeled button that deployed nothing
  // and read as broken. With no history there is nothing to redeploy,
  // so the button falls back to navigating there.
  const latestDeploys = useQuery<{ astroliftDeployments: AstroliftDeployment[] }>(
    LIST_DEPLOYMENTS,
    {
      variables: { appSlug: slug, limit: 1 },
    }
  );
  const latestDeploy = latestDeploys.data?.astroliftDeployments?.[0] ?? null;
  const [headerDeploy, { loading: headerDeploying }] = useMutation<{
    startDeployment: MutationResult<AstroliftDeployment>;
  }>(START_DEPLOYMENT, {
    refetchQueries: [{ query: LIST_DEPLOYMENTS, variables: { appSlug: slug, limit: 1 } }],
  });

  async function onHeaderDeploy() {
    if (!latestDeploy) return;
    let data;
    try {
      ({ data } = await headerDeploy({
        variables: {
          input: {
            appSlug: slug,
            environmentName: latestDeploy.environmentName,
            imageTag: latestDeploy.imageTag,
            triggerKind: "manual",
          },
        },
      }));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : tDetail("headerDeploy.failed"));
      return;
    }
    if (data?.startDeployment.ok) {
      toast.success(
        tDetail("headerDeploy.started", {
          env: latestDeploy.environmentName,
          tag: latestDeploy.imageTag,
        })
      );
    } else {
      toast.error(data?.startDeployment.errors?.[0]?.message ?? tDetail("headerDeploy.failed"));
    }
  }

  // includeDrift opts the resolver into the config-drift rollup
  // (#407 C). The overview is the only caller that needs it; sibling
  // queries that hit GET_APP without the flag keep the cheap shape.
  const app = useQuery<AppResp>(GET_APP, {
    variables: { slug, includeDrift: true },
  });
  const workloads = useQuery<WorkloadsResp>(LIST_WORKLOADS, {
    variables: { appSlug: slug },
  });

  const [softDelete, { loading: deleting }] = useMutation<{
    softDeleteApp: MutationResult<{ id: string; deleted: boolean }>;
  }>(SOFT_DELETE_APP, {
    refetchQueries: [{ query: LIST_APPS }],
    awaitRefetchQueries: true,
  });

  const a = app.data?.astroliftApp ?? null;

  /** Throws on failure so the confirm dialog stays open and shows it. */
  async function onDelete() {
    if (!a) return;
    const { data } = await softDelete({ variables: { input: { id: a.id } } });
    if (data?.softDeleteApp.ok) {
      toast.success(`Deleted ${a.slug}`);
      router.push("/apps");
    } else {
      throw new Error(data?.softDeleteApp.errors?.[0]?.message ?? "Delete failed");
    }
  }

  return {
    loading: app.loading && !app.data,
    app: a,
    workloads: workloads.data?.astroliftWorkloads ?? [],
    latestDeploy,
    headerDeploying,
    onHeaderDeploy,
    deleting,
    onDelete,
  };
}
