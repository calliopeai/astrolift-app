"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { useRouter } from "next/navigation";
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

export type StartField = "appSlug" | "environmentName" | "imageTag" | "imageDigest" | "triggerKind";

export type StartResult =
  | { ok: true }
  | { ok: false; fieldErrors: Partial<Record<StartField, string>>; formError: string | null };

const FIELDS: StartField[] = [
  "appSlug",
  "environmentName",
  "imageTag",
  "imageDigest",
  "triggerKind",
];

/** A backend field name (`image_tag`, `imageTag`) as the page's field, or null. */
function fieldOf(name: string | null | undefined): StartField | null {
  if (!name) return null;
  const camel = name.replace(/_([a-z])/g, (_, c: string) => c.toUpperCase());
  return FIELDS.find((f) => f === camel) ?? null;
}

/**
 * The start-deployment page's data: the app list, the chosen app's
 * environments and the start mutation. The data half of
 * StartDeploymentPage. The chosen app lives here because the environment
 * query keys on it. A started deployment opens its run page; a refusal
 * comes back as field errors to show in place (spec 44 §5.4).
 */
export function useStartDeployment() {
  const router = useRouter();
  const apps = useQuery<AppsResp>(LIST_APPS);
  const [appSlug, setAppSlug] = React.useState("");
  const envs = useQuery<EnvsResp>(LIST_ENVIRONMENTS, {
    variables: { appSlug },
    skip: !appSlug,
  });

  const [start, { loading }] = useMutation<MutationResp>(START_DEPLOYMENT, {
    // By operation name: the list is cursor paged, so no literal variables
    // object matches the page an operator returns to.
    refetchQueries: ["ListDeploymentsPage"],
  });

  async function onSubmit(input: StartDeploymentInput): Promise<StartResult> {
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
    const res = data?.startDeployment;
    if (res?.ok) {
      toast.success(`Deployment started: ${res.data?.status}`);
      router.push(res.data ? `/deployments/${res.data.id}` : "/deployments");
      return { ok: true };
    }
    const fieldErrors: Partial<Record<StartField, string>> = {};
    let formError: string | null = null;
    for (const e of res?.errors ?? []) {
      const field = fieldOf(e.field);
      if (field) fieldErrors[field] = e.message;
      else formError = formError ?? e.message;
    }
    if (!res) formError = "The deployment could not be started.";
    return { ok: false, fieldErrors, formError };
  }

  return {
    apps: apps.data?.astroliftApps ?? [],
    appsLoading: apps.loading && !apps.data,
    appsError: apps.error ? apps.error.message : null,
    environments: envs.data?.astroliftEnvironments ?? [],
    environmentsLoading: envs.loading && !envs.data,
    appSlug,
    setAppSlug,
    submitting: loading,
    onSubmit,
    cancelHref: "/deployments",
  };
}
