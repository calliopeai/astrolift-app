"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import * as React from "react";

import type {
  ActionApprovalRequestsQuery,
  ActionSafetyQuery,
  ApproveActionRequestMutation,
  CancelActionRequestMutation,
  RejectActionRequestMutation,
  SetActionSafetyRuleMutation,
  SetActionSafetyRuleInput,
  SetActionSafetyRuleMutationVariables,
} from "@/graphql/__generated__/operations";
import {
  APPROVE_ACTION_REQUEST,
  CANCEL_ACTION_REQUEST,
  REJECT_ACTION_REQUEST,
  SET_ACTION_SAFETY_RULE,
} from "@/graphql/permissions/action-safety.mutations";
import { ACTION_APPROVAL_REQUESTS, ACTION_SAFETY } from "@/graphql/permissions/astrolift.queries";

export type ActionSafetyRow = ActionSafetyQuery["astroliftActionSafety"][number];
export type ActionApprovalRequestRow =
  ActionApprovalRequestsQuery["astroliftActionApprovalRequests"][number];

/** The organization switches one write may change; the server leaves the rest as they are. */
export type ActionSafetyPatch = Partial<
  Pick<
    SetActionSafetyRuleInput,
    "enabled" | "allowSelfApproval" | "requireApproval" | "minimumApprovals"
  >
>;

// Null leaves a switch as it is on the server.
const UNCHANGED = {
  enabled: null,
  allowSelfApproval: null,
  requireApproval: null,
  minimumApprovals: null,
};

export type ApprovalVerb = "approve" | "reject" | "cancel";

const OPEN = new Set(["pending", "approved"]);

/**
 * The organization's action-safety gates (#2203): what each asks, the
 * org-level switches, and the approval requests still open (#2202). A refusal
 * for a fresh sign-in comes back as STEP_UP_REQUIRED, which the app-wide
 * prompt handles with a retry.
 */
export function useActionSafety() {
  const query = useQuery<ActionSafetyQuery>(ACTION_SAFETY, { fetchPolicy: "cache-and-network" });
  const requests = useQuery<ActionApprovalRequestsQuery>(ACTION_APPROVAL_REQUESTS, {
    fetchPolicy: "cache-and-network",
  });
  const [mutate] = useMutation<SetActionSafetyRuleMutation, SetActionSafetyRuleMutationVariables>(
    SET_ACTION_SAFETY_RULE
  );
  const [approve] = useMutation<ApproveActionRequestMutation>(APPROVE_ACTION_REQUEST);
  const [reject] = useMutation<RejectActionRequestMutation>(REJECT_ACTION_REQUEST);
  const [cancel] = useMutation<CancelActionRequestMutation>(CANCEL_ACTION_REQUEST);
  const [saving, setSaving] = React.useState<string | null>(null);
  const [saveError, setSaveError] = React.useState<string | null>(null);
  const [deciding, setDeciding] = React.useState<string | null>(null);
  const [decideError, setDecideError] = React.useState<string | null>(null);

  async function setRule(action: string, patch: ActionSafetyPatch) {
    setSaving(action);
    setSaveError(null);
    try {
      const result = await mutate({
        variables: { input: { action, scopeKind: "ORG", scopeId: null, ...UNCHANGED, ...patch } },
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

  async function decide(id: string, verb: ApprovalVerb) {
    setDeciding(id);
    setDecideError(null);
    try {
      const variables = { input: { id } };
      const result =
        verb === "approve"
          ? (await approve({ variables })).data?.approveActionRequest
          : verb === "reject"
            ? (await reject({ variables })).data?.rejectActionRequest
            : (await cancel({ variables })).data?.cancelActionRequest;
      if (result?.ok === false)
        setDecideError(result.errors[0]?.message ?? "The decision was not saved.");
      await requests.refetch();
    } catch (error) {
      setDecideError(error instanceof Error ? error.message : "The decision was not saved.");
    } finally {
      setDeciding(null);
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
    requests: (requests.data?.astroliftActionApprovalRequests ?? []).filter((r) =>
      OPEN.has(r.status)
    ),
    requestsError: requests.error?.message ?? null,
    deciding,
    decideError,
    onDecide: decide,
  };
}
