"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import * as React from "react";

import type { SearchedPrincipal } from "@/components/access/use-grant-access";
import { useScopeTree } from "@/components/access/use-scope-tree";
import { PRINCIPAL_SEARCH } from "@/graphql/access/access.queries";
import type {
  ActionApprovalRequestsQuery,
  ActionSafetyQuery,
  ActionSafetyRulesQuery,
  ClearActionSafetyRuleMutation,
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
  CLEAR_ACTION_SAFETY_RULE,
  REJECT_ACTION_REQUEST,
  SET_ACTION_SAFETY_RULE,
} from "@/graphql/permissions/action-safety.mutations";
import {
  ACTION_APPROVAL_REQUESTS,
  ACTION_SAFETY,
  ACTION_SAFETY_RULES,
} from "@/graphql/permissions/astrolift.queries";
import { useDebounce } from "@/hooks/use-debounce";

import type {
  ScopedKind,
  ScopedRule,
  ScopedRulePatch,
  ScopedSafetyRulesProps,
} from "./ScopedSafetyRules";

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
  // Null shows the organization's switches; an app slug shows, read-only, what
  // applies there after team, app and person rules.
  const [inspecting, setInspecting] = React.useState<string | null>(null);
  const query = useQuery<ActionSafetyQuery>(ACTION_SAFETY, {
    variables: { appSlug: inspecting },
    fetchPolicy: "cache-and-network",
  });
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
    inspecting,
    onInspect: setInspecting,
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
    scoped: useScopedSafetyRules(query.data?.astroliftActionSafety ?? []),
  };
}

/** Team, app and person rules (#2203): the list, the pickers, add and remove. */
function useScopedSafetyRules(rows: ActionSafetyRow[]): ScopedSafetyRulesProps {
  const rules = useQuery<ActionSafetyRulesQuery>(ACTION_SAFETY_RULES, {
    fetchPolicy: "cache-and-network",
  });
  const [write] = useMutation<SetActionSafetyRuleMutation, SetActionSafetyRuleMutationVariables>(
    SET_ACTION_SAFETY_RULE
  );
  const [clear] = useMutation<ClearActionSafetyRuleMutation>(CLEAR_ACTION_SAFETY_RULE);
  const scopeTree = useScopeTree();
  const [query, setQuery] = React.useState("");
  const term = useDebounce(query.trim(), 250);
  const search = useQuery<{ astroliftPrincipalSearch: { items: SearchedPrincipal[] } }>(
    PRINCIPAL_SEARCH,
    {
      variables: { search: term, filter: { kind: ["USER"] }, page: 1, pageSize: 8 },
      skip: !term,
      fetchPolicy: "cache-first",
    }
  );
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  async function run(step: () => Promise<{ ok: boolean; message?: string } | undefined>) {
    setSaving(true);
    setError(null);
    try {
      const result = await step();
      if (result && !result.ok) {
        setError(result.message ?? "The rule was not saved.");
        return false;
      }
      await rules.refetch();
      return true;
    } catch (e) {
      setError(e instanceof Error ? e.message : "The rule was not saved.");
      return false;
    } finally {
      setSaving(false);
    }
  }

  return {
    actions: rows.map(({ action, label, approvable }) => ({ action, label, approvable })),
    rules: (rules.data?.astroliftActionSafetyRules ?? []).filter((r) => r.scopeKind !== "ORG"),
    scopeTree,
    people: {
      query,
      onQuery: setQuery,
      results: (search.data?.astroliftPrincipalSearch.items ?? []).flatMap((p) =>
        p.kind === "USER" && p.userId
          ? [{ id: p.userId, name: p.name, secondary: p.secondary }]
          : []
      ),
      loading: search.loading,
    },
    saving,
    error: error ?? rules.error?.message ?? null,
    onAdd: (target: { kind: ScopedKind; id: string }, action: string, patch: ScopedRulePatch) =>
      run(async () => {
        const payload = (
          await write({
            variables: {
              input: { action, scopeKind: target.kind, scopeId: target.id, ...UNCHANGED, ...patch },
            },
          })
        ).data?.setActionSafetyRule;
        return payload && { ok: payload.ok, message: payload.errors[0]?.message };
      }),
    onClear: (rule: ScopedRule) =>
      void run(async () => {
        const payload = (
          await clear({
            variables: {
              input: {
                action: rule.action,
                scopeKind: rule.scopeKind,
                scopeId: rule.scopeId ?? null,
              },
            },
          })
        ).data?.clearActionSafetyRule;
        return payload && { ok: payload.ok, message: payload.errors[0]?.message };
      }),
  };
}
