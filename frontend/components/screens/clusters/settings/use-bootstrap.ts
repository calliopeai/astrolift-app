"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

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
  const t = useTranslations("clusterSettings.bootstrapPlan");
  const permissions = useMyPermissions();
  const allowed =
    (permissions.loading && permissions.granted.size === 0) || permissions.can("cluster.manage");
  const { data, loading, error, refetch } = useQuery<BootstrapPlanResp>(CLUSTER_BOOTSTRAP_PLAN, {
    variables: { clusterId },
    fetchPolicy: "cache-and-network",
    notifyOnNetworkStatusChange: true,
  });
  const plan = data?.astroliftClusterBootstrapPlan ?? null;
  const unavailable = !!error || (!loading && data?.astroliftClusterBootstrapPlan === undefined);
  const observed = !!plan && plan.clusterId === clusterId && !unavailable && !loading;
  const fingerprint = JSON.stringify([clusterId, plan, observed, allowed]);
  const context = React.useMemo(() => ({ fingerprint }), [fingerprint]);
  const current = React.useRef<object | null>(context);
  React.useLayoutEffect(() => {
    current.current = context;
    return () => {
      current.current = null;
    };
  }, [context]);
  const [pending, setPending] = React.useState<object | null>(null);
  const running = React.useRef<object | null>(null);

  const [install] = useMutation<{
    installClusterPrereqs: MutationResult<{ id: string; slug: string }>;
  }>(INSTALL_CLUSTER_PREREQS, { fetchPolicy: "no-cache" });

  async function onInstall(
    selected: Record<string, boolean>,
    optionValues: Record<string, Record<string, string>>
  ) {
    if (current.current !== context || !observed || !allowed || !plan) {
      toast.error(t("sourceChanged"));
      return;
    }
    if (running.current === context) return;
    const selectedComponents = plan.components.filter((c) => selected[c.key]).map((c) => c.key);
    const optionOverrides: { componentKey: string; optionKey: string; value: string }[] = [];
    for (const c of plan.components) {
      if (!selected[c.key]) continue;
      for (const o of c.options) {
        const v = optionValues[c.key]?.[o.key];
        if (v && !o.choices.some((choice) => choice.value === v)) {
          toast.error(t("invalidOption"));
          return;
        }
        if (v && v !== o.default) {
          optionOverrides.push({ componentKey: c.key, optionKey: o.key, value: v });
        }
      }
    }
    running.current = context;
    setPending(context);
    try {
      const { data } = await install({
        variables: {
          input: { clusterId, selectedComponents, optionOverrides },
        },
      });
      if (data?.installClusterPrereqs.ok) {
        toast.success(t("requested", { count: selectedComponents.length, clusterId }));
      } else {
        toast.error(data?.installClusterPrereqs.errors?.[0]?.message || t("failed"));
      }
    } catch (error) {
      toast.error(error instanceof Error && error.message ? error.message : t("failed"));
    } finally {
      if (running.current === context) running.current = null;
      setPending((active) => (active === context ? null : active));
    }
  }

  return {
    plan,
    loading,
    installing: pending === context,
    onInstall,
    readOnly: !observed || !allowed,
    error:
      error?.message ||
      (unavailable || (!!plan && plan.clusterId !== clusterId) ? t("unavailable") : null),
    onRetry: () => {
      void refetch().catch(() => undefined);
    },
  };
}

/**
 * The per-cluster bootstrap history (#319). Only run while the history
 * disclosure is open. The data half of BootstrapHistoryView.
 */
export function useBootstrapHistory(slug: string) {
  const t = useTranslations("clusterSettings.bootstrapHistory");
  const { data, loading, error, refetch } = useQuery<BootstrapRunsResp>(CLUSTER_BOOTSTRAP_RUNS, {
    variables: { slug, limit: 10 },
    fetchPolicy: "cache-and-network",
    notifyOnNetworkStatusChange: true,
  });
  const source = data?.astroliftCluster;
  const unavailable =
    !loading && (!source || source.slug !== slug || !Array.isArray(source.bootstrapRuns));
  return {
    runs: source?.slug === slug ? (source.bootstrapRuns ?? []) : [],
    loading,
    error: error?.message || (unavailable ? t("unavailable") : null),
    onRetry: () => {
      void refetch().catch(() => undefined);
    },
  };
}
