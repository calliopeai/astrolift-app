"use client";

import { useQuery } from "@apollo/client/react";

import type {
  PermissionCompareQuery,
  PermissionDiagnoseQuery,
} from "@/graphql/__generated__/operations";
import { PERMISSION_COMPARE, PERMISSION_DIAGNOSE } from "@/graphql/access/access.queries";

import type { ScopeRef } from "./access-model";
import type { Comparison, Diagnosis } from "./AccessExplainer";

/** The optional target both analyses take: `scopeType` and the object's guid. */
function target(scope: Pick<ScopeRef, "kind" | "id"> | null | undefined) {
  return { scopeType: scope?.kind ?? null, scopeId: scope?.id ?? null };
}

/**
 * The data half of `AccessExplainer`: `permissionDiagnose` for one person
 * and one permission, on `scope` when one is picked (the resolver's answer
 * for that object, inheritance and team shares included), else anywhere in
 * the org. Runs only once both are chosen, and network-only, because the
 * point of asking is the answer right now.
 */
export function useAccessExplainer(
  userId: string | null,
  permission: string | null,
  scope?: Pick<ScopeRef, "kind" | "id"> | null
) {
  const { data, loading, error, refetch } = useQuery<PermissionDiagnoseQuery>(PERMISSION_DIAGNOSE, {
    variables: { userId: userId ?? "", permission: permission ?? "", ...target(scope) },
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

/** The data half of `AccessCompare`: `permissionCompare`, once both people are chosen, on `scope` if any. */
export function useAccessCompare(
  userIdA: string | null,
  userIdB: string | null,
  scope?: Pick<ScopeRef, "kind" | "id"> | null
) {
  const { data, loading, error, refetch } = useQuery<PermissionCompareQuery>(PERMISSION_COMPARE, {
    variables: { userIdA: userIdA ?? "", userIdB: userIdB ?? "", ...target(scope) },
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
