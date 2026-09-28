"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import * as React from "react";
import { toast } from "sonner";

import { START_DEPLOYMENT } from "@/graphql/lifecycle/lifecycle.mutations";
import { LIST_ENVIRONMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import type {
  AstroliftAppEnvironment,
  AstroliftDeployment,
} from "@/graphql/lifecycle/lifecycle.types";
import { LIST_APPS } from "@/graphql/registry/registry.queries";

import type { TriggerKind } from "./deployments-format";

export interface AppListItem {
  id: string;
  slug: string;
  name: string;
}

interface AppsResp {
  astroliftApps: AppListItem[];
}

interface EnvsResp {
  astroliftEnvironments: AstroliftAppEnvironment[];
}

interface MutationResp {
  startDeployment: {
    ok: boolean;
    errors: { code: string; message: string; field?: string | null }[];
    data: AstroliftDeployment | null;
  };
}

export interface StartDeploymentInput {
  environmentName: string;
  imageTag: string;
  imageDigest: string;
  triggerKind: TriggerKind;
}

/**
 * The start-deployment sheet's data: the app list, the chosen app's
 * environments and the start mutation. The data half of
 * StartDeploymentSheet. The chosen app lives here because the
 * environment query keys on it. `onSubmit` resolves true when the
 * deployment started, so the sheet can close.
 */
export function useStartDeployment(open: boolean) {
  const apps = useQuery<AppsResp>(LIST_APPS, { skip: !open });
  const [appSlug, setAppSlug] = React.useState("");
  const envs = useQuery<EnvsResp>(LIST_ENVIRONMENTS, {
    variables: { appSlug },
    skip: !open || !appSlug,
  });

  React.useEffect(() => {
    if (!open) setAppSlug("");
  }, [open]);

  const [start, { loading }] = useMutation<MutationResp>(START_DEPLOYMENT, {
    // By operation name: the list this dialog feeds is cursor-paginated
    // now, so its variables carry the tab filter, page cursor and search
    // term and no literal variables object would match the query the
    // operator is looking at.
    refetchQueries: ["ListDeploymentsPage", "DeploymentTabCounts"],
    awaitRefetchQueries: true,
  });

  async function onSubmit(input: StartDeploymentInput): Promise<boolean> {
    const { data } = await start({
      variables: {
        input: {
          appSlug,
          environmentName: input.environmentName,
          imageTag: input.imageTag,
          imageDigest: input.imageDigest.trim() || null,
          triggerKind: input.triggerKind,
        },
      },
    });
    if (data?.startDeployment.ok) {
      toast.success(`Deployment started: ${data.startDeployment.data?.status}`);
      return true;
    }
    toast.error(data?.startDeployment.errors[0]?.message ?? "Failed");
    return false;
  }

  return {
    apps: apps.data?.astroliftApps ?? [],
    environments: envs.data?.astroliftEnvironments ?? [],
    appSlug,
    setAppSlug,
    submitting: loading,
    onSubmit,
  };
}
