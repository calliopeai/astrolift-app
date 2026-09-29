"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { toast } from "sonner";

import {
  CLUSTER_BOOTSTRAP_PLAN,
  CLUSTER_BOOTSTRAP_RUNS,
  INSTALL_CLUSTER_PREREQS,
} from "@/graphql/clusters/clusters.queries";
import type { MutationResult } from "@/graphql/identity/identity.types";

import type { BootstrapPlan, BootstrapRun } from "./types";

interface BootstrapPlanResp {
  astroliftClusterBootstrapPlan: BootstrapPlan | null;
}

interface BootstrapRunsResp {
  astroliftCluster: { id: string; slug: string; bootstrapRuns: BootstrapRun[] } | null;
}

/**
 * The driver's bootstrap recipe and the install that applies a selection
 * of it (#67 + #66). The data half of BootstrapPlanView.
 */
export function useBootstrapPlan(clusterId: string) {
  const { data, loading } = useQuery<BootstrapPlanResp>(CLUSTER_BOOTSTRAP_PLAN, {
    variables: { clusterId },
  });
  const plan = data?.astroliftClusterBootstrapPlan ?? null;

  const [install, { loading: installing }] = useMutation<{
    installClusterPrereqs: MutationResult<{ id: string; slug: string }>;
  }>(INSTALL_CLUSTER_PREREQS);

  async function onInstall(
    selected: Record<string, boolean>,
    optionValues: Record<string, Record<string, string>>
  ) {
    if (!plan) return;
    const selectedComponents = plan.components.filter((c) => selected[c.key]).map((c) => c.key);
    const optionOverrides: { componentKey: string; optionKey: string; value: string }[] = [];
    for (const c of plan.components) {
      if (!selected[c.key]) continue;
      for (const o of c.options) {
        const v = optionValues[c.key]?.[o.key];
        if (v && v !== o.default) {
          optionOverrides.push({ componentKey: c.key, optionKey: o.key, value: v });
        }
      }
    }
    const { data } = await install({
      variables: {
        input: { clusterId, selectedComponents, optionOverrides },
      },
    });
    if (data?.installClusterPrereqs.ok) {
      toast.success(`Installing ${selectedComponents.length} prereq(s)`);
    } else {
      toast.error(data?.installClusterPrereqs.errors?.[0]?.message ?? "Install failed");
    }
  }

  return { plan, loading, installing, onInstall };
}

/**
 * The per-cluster bootstrap history (#319). Only run while the history
 * disclosure is open. The data half of BootstrapHistoryView.
 */
export function useBootstrapHistory(slug: string) {
  const { data, loading } = useQuery<BootstrapRunsResp>(CLUSTER_BOOTSTRAP_RUNS, {
    variables: { slug, limit: 10 },
  });
  return { runs: data?.astroliftCluster?.bootstrapRuns ?? [], loading };
}
