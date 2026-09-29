"use client";

import { useQuery } from "@apollo/client/react";

import type {
  PermissionCompareQuery,
  PermissionCompareQueryVariables,
  PermissionDiagnoseQuery,
  PermissionDiagnoseQueryVariables,
} from "@/graphql/__generated__/operations";
import { PERMISSION_COMPARE, PERMISSION_DIAGNOSE } from "@/graphql/access/access.queries";

import type { Comparison, Diagnosis } from "./AccessExplainer";

/**
 * The data half of `AccessExplainer`: `permissionDiagnose` for one person
 * and one permission. Runs only once both are chosen, and network-only,
 * because the point of asking is the answer right now.
 */
export function useAccessExplainer(userId: string | null, permission: string | null) {
  const { data, loading, error, refetch } = useQuery<
    PermissionDiagnoseQuery,
    PermissionDiagnoseQueryVariables
  >(PERMISSION_DIAGNOSE, {
    variables: { userId: userId ?? "", permission: permission ?? "" },
    skip: !userId || !permission,
    fetchPolicy: "network-only",
  });
  const d = data?.permissionDiagnose;
  const diagnosis: Diagnosis | null = d
    ? {
        username: d.username,
        permission: d.permission,
        granted: d.granted,
        isSuperuser: d.isSuperuser,
        steps: d.steps,
      }
    : null;
  return {
    diagnosis,
    loading: loading && !data,
    error: error ? { message: error.message } : null,
    onRetry: () => void refetch(),
  };
}

/** The data half of `AccessCompare`: `permissionCompare`, once both people are chosen. */
export function useAccessCompare(userIdA: string | null, userIdB: string | null) {
  const { data, loading, error, refetch } = useQuery<
    PermissionCompareQuery,
    PermissionCompareQueryVariables
  >(PERMISSION_COMPARE, {
    variables: { userIdA: userIdA ?? "", userIdB: userIdB ?? "" },
    skip: !userIdA || !userIdB,
    fetchPolicy: "network-only",
  });
  const comparison: Comparison | null = data?.permissionCompare ?? null;
  return {
    comparison,
    loading: loading && !data,
    error: error ? { message: error.message } : null,
    onRetry: () => void refetch(),
  };
}
