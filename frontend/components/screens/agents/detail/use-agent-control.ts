"use client";

import { useMutation } from "@apollo/client/react";
import { toast } from "sonner";

import { UPDATE_AGENT_RUN_SPEC } from "@/graphql/agents/agents.mutations";
import { LIST_AGENT_FLEET } from "@/graphql/agents/agents.queries";
import type {
  AgentRunSpecInput,
  AstroliftAgentListItem,
  AstroliftAgentRunSpec,
} from "@/graphql/agents/agents.types";
import type { MutationResult } from "@/graphql/identity/identity.types";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";

interface UpdateResp {
  updateAgentRunSpec: MutationResult<AstroliftAgentRunSpec>;
}

/**
 * What a run-spec save tells the editor: on success, the persisted spec to
 * re-seed from (null when the backend returned none); on failure, the first
 * server error's field + message when it named a field (null otherwise, e.g. a
 * network error — the toast already said so).
 */
export type SaveRunSpecResult =
  | { ok: true; persisted: AstroliftAgentRunSpec | null }
  | { ok: false; fieldError: { field: string; message: string } | null };

/**
 * The data half of the agent Control tab (spec 33 PR-11 / PR-12): the
 * `updateAgentRunSpec` write, its toasts, and the fleet refetch. The editor
 * state itself lives in AgentControlScreen.
 */
export function useAgentControl(agent: AstroliftAgentListItem) {
  // Refetch the fleet list after a save so the resolved row re-seeds with the
  // persisted run-spec. `updateAgentRunSpec` returns an `AstroliftAgentRunSpec`
  // — a DIFFERENT GraphQL type than the `AstroliftAgentListItem` the shell
  // header + `useAgent` read — so the mutation result does NOT normalize into
  // the list-row cache; without this refetch the header badge (run mode /
  // paused) would show stale values until the fleet query reloaded on its own.
  // Scoped to the same `orgId` `useAgent` resolves against.
  // Reactive org id (#1022): the synchronous cookie read races the
  // post-render effect that sets it, leaving orgId "" on cold load.
  const { org } = useActiveOrg();
  const orgId = org?.id ?? "";
  const [save, { loading: saving }] = useMutation<UpdateResp>(UPDATE_AGENT_RUN_SPEC, {
    refetchQueries: orgId ? [{ query: LIST_AGENT_FLEET, variables: { orgId } }] : [],
  });

  async function onSave(input: AgentRunSpecInput): Promise<SaveRunSpecResult> {
    try {
      const { data } = await save({ variables: { agentSlug: agent.slug, input } });
      const result = data?.updateAgentRunSpec;
      if (result?.ok) {
        toast.success(`Run spec saved for ${agent.name}`);
        return { ok: true, persisted: result.data ?? null };
      }
      const err = result?.errors?.[0];
      toast.error(`Couldn't save run spec`, {
        description: err?.message ?? "Unknown error",
      });
      return {
        ok: false,
        fieldError: err?.field ? { field: err.field, message: err.message } : null,
      };
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e);
      toast.error(`Couldn't save run spec for ${agent.name}`, { description: message });
      return { ok: false, fieldError: null };
    }
  }

  return { agent, orgId, saving, onSave };
}
