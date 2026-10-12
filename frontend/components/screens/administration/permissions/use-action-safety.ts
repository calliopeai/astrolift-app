"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import * as React from "react";

import type {
  ActionSafetyQuery,
  SetActionSafetyRuleMutation,
  SetActionSafetyRuleMutationVariables,
} from "@/graphql/__generated__/operations";
import { SET_ACTION_SAFETY_RULE } from "@/graphql/permissions/action-safety.mutations";
import { ACTION_SAFETY } from "@/graphql/permissions/astrolift.queries";

export type ActionSafetyRow = ActionSafetyQuery["astroliftActionSafety"][number];

/**
 * The organization's action-safety gates (#2203): what each asks, and the
 * org-level switches. A refusal for a fresh sign-in comes back as
 * STEP_UP_REQUIRED, which the app-wide prompt handles with a retry.
 */
export function useActionSafety() {
  const query = useQuery<ActionSafetyQuery>(ACTION_SAFETY, { fetchPolicy: "cache-and-network" });
  const [mutate] = useMutation<SetActionSafetyRuleMutation, SetActionSafetyRuleMutationVariables>(
    SET_ACTION_SAFETY_RULE
  );
  const [saving, setSaving] = React.useState<string | null>(null);
  const [saveError, setSaveError] = React.useState<string | null>(null);

  // The rule is written whole: both switches, as they are after this change.
  async function setRule(action: string, rule: { enabled: boolean; allowSelfApproval: boolean }) {
    setSaving(action);
    setSaveError(null);
    try {
      const result = await mutate({
        variables: { input: { action, scopeKind: "ORG", scopeId: null, ...rule } },
      });
      const payload = result.data?.setActionSafetyRule;
      if (payload?.ok === false)
        setSaveError(payload.errors[0]?.message ?? "The rule was not saved.");
      else await query.refetch();
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : "The rule was not saved.");
    } finally {
      setSaving(null);
    }
  }

  return {
    rows: query.data?.astroliftActionSafety ?? null,
    loading: query.loading && !query.data,
    error: query.error?.message ?? null,
    onRetry: (): void => {
      void query.refetch().catch(() => {});
    },
    saving,
    saveError,
    onSetRule: setRule,
  };
}
