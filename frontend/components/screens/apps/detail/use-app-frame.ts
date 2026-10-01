"use client";

import { useApolloClient, useMutation, useQuery } from "@apollo/client/react";
import { usePathname, useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import type { MutationResult } from "@/graphql/identity/identity.types";
import { START_DEPLOYMENT } from "@/graphql/lifecycle/lifecycle.mutations";
import { LIST_DEPLOYMENTS, LIST_ENVIRONMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftDeployment } from "@/graphql/lifecycle/lifecycle.types";
import { SOFT_DELETE_APP } from "@/graphql/registry/registry.mutations";
import { GET_APP, LIST_APPS, LIST_WORKLOADS } from "@/graphql/registry/registry.queries";
import type { AstroliftRegisteredApp, AstroliftWorkload } from "@/graphql/registry/registry.types";
import { type PermissionCheck, useMyPermissions } from "@/lib/permissions/use-my-permissions";

import { useArchiveApp } from "../settings/use-archive-app";

import type { AppFrameApp, AppFrameProps } from "./AppFrame";
import { appDeploysVariables, useAppDeploys } from "./use-app-deploys";

interface AppResp {
  astroliftApp: AstroliftRegisteredApp | null;
}
interface EnvsResp {
  astroliftEnvironments: { id: string; name: string; clusterSlug?: string | null }[];
}
interface WorkloadsResp {
  astroliftWorkloads: AstroliftWorkload[];
}

/**
 * The data half of AppFrame: the app, where it runs, the latest deployment
 * the header's Deploy re-runs (from the deploys read it shares with the
 * Overview), and the Deploy, Archive, Restore, Delete and
 * Copy ID actions. The same queries and permissions the overview header
 * used (`app.deploy`, `app.update`, `app.delete`).
 */
export function useAppFrame(slug: string): Omit<AppFrameProps, "children"> {
  const router = useRouter();
  const client = useApolloClient();
  const pathname = usePathname() ?? "";
  const tDetail = useTranslations("apps.detail");
  const tFrame = useTranslations("apps.frame");
  const perms = useMyPermissions();
  // Optimistic while the permission set loads, as `Can` is; the backend
  // still enforces every one.
  const allow = (p: PermissionCheck) => (perms.loading && perms.granted.size === 0) || perms.can(p);

  const appQ = useQuery<AppResp>(GET_APP, { variables: { slug } });
  const workloadsQ = useQuery<WorkloadsResp>(LIST_WORKLOADS, { variables: { appSlug: slug } });
  const envsQ = useQuery<EnvsResp>(LIST_ENVIRONMENTS, { variables: { appSlug: slug } });
  // The Overview's panels read the same deploys (one query, see use-app-deploys).
  const latest = useAppDeploys(slug, { live: true }).deployments[0] ?? null;

  const [startDeploy, { loading: deploying }] = useMutation<{
    startDeployment: MutationResult<AstroliftDeployment>;
  }>(START_DEPLOYMENT, {
    refetchQueries: [{ query: LIST_DEPLOYMENTS, variables: appDeploysVariables(slug) }],
  });
  const [softDelete, { loading: deleting }] = useMutation<{
    softDeleteApp: MutationResult<{ id: string; deleted: boolean }>;
  }>(SOFT_DELETE_APP);
  const archive = useArchiveApp(slug);

  const a = appQ.data?.astroliftApp ?? null;
  const envs = envsQ.data?.astroliftEnvironments ?? [];
  // Name the environment the latest deploy went to; otherwise the first.
  const env = envs.find((e) => e.name === latest?.environmentName) ?? envs[0] ?? null;
  // Open app only when something is public, as the overview header had it:
  // the managed hostname, else the short subdomain.
  const isPublic = (workloadsQ.data?.astroliftWorkloads ?? []).some((w) => w.isPublic);
  const host = isPublic ? a?.managedHostname || a?.subdomain || null : null;

  const app: AppFrameApp | null = a
    ? {
        id: a.id,
        slug: a.slug,
        name: a.name,
        status: a.provisioningStatus,
        live: Boolean(a.managedHostname),
        archived: Boolean(a.isArchived),
        failureReason: a.provisioningError || null,
        environment: env ? { name: env.name, clusterSlug: env.clusterSlug ?? null } : null,
        moreEnvironments: Math.max(0, envs.length - 1),
        appUrl: host ? `https://${host}` : null,
        repoUrl: a.sourceUrl || null,
      }
    : null;

  async function onDeploy() {
    if (!latest) return;
    let data;
    try {
      ({ data } = await startDeploy({
        variables: {
          input: {
            appSlug: slug,
            environmentName: latest.environmentName,
            imageTag: latest.imageTag,
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
        tDetail("headerDeploy.started", { env: latest.environmentName, tag: latest.imageTag })
      );
    } else {
      toast.error(data?.startDeployment.errors?.[0]?.message ?? tDetail("headerDeploy.failed"));
    }
  }

  /** Failed/refused writes keep confirmation open; later failures retain acceptance. */
  async function onDelete() {
    if (!a) return;
    const deletedSlug = a.slug;
    let data;
    try {
      ({ data } = await softDelete({ variables: { input: { id: a.id } } }));
    } catch (error) {
      throw new Error(
        error instanceof Error && error.message
          ? error.message
          : typeof error === "string" && error
            ? error
            : tFrame("deleteFeedback.failed")
      );
    }
    if (!data?.softDeleteApp.ok)
      throw new Error(data?.softDeleteApp.errors?.[0]?.message || tFrame("deleteFeedback.failed"));
    toast.success(tFrame("deleteFeedback.deleted", { slug: deletedSlug }));
    function warn(key: "refreshWarning" | "navigationWarning", error: unknown) {
      toast.warning(tFrame(`deleteFeedback.${key}`, { slug: deletedSlug }), {
        description:
          error instanceof Error ? error.message : typeof error === "string" ? error : undefined,
      });
    }
    try {
      await client.query({ query: LIST_APPS, fetchPolicy: "network-only" });
    } catch (error) {
      warn("refreshWarning", error);
    }
    try {
      router.push("/apps");
    } catch (error) {
      warn("navigationWarning", error);
    }
  }

  function onCopyId() {
    if (!a) return;
    void navigator.clipboard?.writeText(a.id).then(
      () => toast.success(tFrame("menu.copied")),
      () => toast.error(a.id)
    );
  }

  return {
    slug,
    pathname,
    app,
    loading: appQ.loading && !appQ.data,
    error: appQ.error && !appQ.data ? appQ.error.message : null,
    onRetry: () => void appQ.refetch(),
    latestDeploy: latest
      ? { imageTag: latest.imageTag, environmentName: latest.environmentName }
      : null,
    canDeploy: allow("app.deploy"),
    deploying,
    onDeploy,
    canUpdate: allow("app.update"),
    archiving: archive.archiving || archive.restoring,
    onArchive: archive.onArchive,
    onRestore: archive.onRestore,
    canDelete: allow("app.delete"),
    deleting,
    onDelete,
    onCopyId,
  };
}
