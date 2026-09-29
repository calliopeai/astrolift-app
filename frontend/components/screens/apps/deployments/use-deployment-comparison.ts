"use client";

import { useQuery } from "@apollo/client/react";

import { COMPARE_DEPLOYMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftDeploymentComparison } from "@/graphql/lifecycle/lifecycle.types";

/**
 * Deploy-vs-deploy comparison (#652). The data half of
 * CompareDeploymentsSheetView; skipped while the sheet is closed.
 */
export function useDeploymentComparison(idA: string, idB: string, open: boolean) {
  const { data, loading, error } = useQuery<{
    astroliftCompareDeployments: AstroliftDeploymentComparison | null;
  }>(COMPARE_DEPLOYMENTS, {
    variables: { idA, idB },
    skip: !open,
  });

  return {
    comparison: data?.astroliftCompareDeployments ?? null,
    loading,
    /** The query's error message, when it failed. */
    error: error?.message ?? null,
  };
}
