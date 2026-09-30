"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { toast } from "sonner";

import { CREATE_AGENT_TRIGGER, UNBIND_AGENT_TRIGGER } from "@/graphql/agents/agents.mutations";
import { AGENT_TRIGGERS } from "@/graphql/agents/agents.queries";
import type {
  AgentTriggersData,
  AgentTriggersVars,
  AstroliftAgentTriggerResult,
} from "@/graphql/agents/agents.types";

// Hand-rolled write envelopes, matching how the agents area types its mutation
// responses inline (see control-content's UpdateResp / dispatch-tab's
// RunAgentResp) rather than consuming codegen op-types. Both resolve to the flat
// AstroliftAgentTriggerResult (ok + message — no errors[] list).
interface CreateTriggerResp {
  createAgentTrigger: AstroliftAgentTriggerResult;
}
interface UnbindTriggerResp {
  unbindAgentTrigger: AstroliftAgentTriggerResult;
}

export interface CreateTriggerInput {
  scmRepo: string;
  branchPattern: string;
  inputMapping: Record<string, unknown> | null;
}

/**
 * The trigger bindings behind TriggerBindingEditorView: the list query plus the
 * bind and unbind mutations, with their toasts.
 */
export function useTriggerBindings({
  agentSlug,
  agentName,
  orgId,
}: {
  agentSlug: string;
  agentName: string;
  orgId: string;
}) {
  const { data, loading, error, refetch } = useQuery<AgentTriggersData, AgentTriggersVars>(
    AGENT_TRIGGERS,
    {
      variables: { orgId, agentSlug },
      skip: !orgId,
      fetchPolicy: "cache-and-network",
    }
  );
  const triggers = data?.agentTriggers ?? [];

  // Refetch the list after either write so a bind/unbind reflects immediately.
  // Variables must match the watched query exactly for Apollo to refetch it.
  const refetchQueries = orgId ? [{ query: AGENT_TRIGGERS, variables: { orgId, agentSlug } }] : [];
  const [createTrigger, { loading: creating }] = useMutation<CreateTriggerResp>(
    CREATE_AGENT_TRIGGER,
    { refetchQueries }
  );
  const [unbindTrigger] = useMutation<UnbindTriggerResp>(UNBIND_AGENT_TRIGGER, { refetchQueries });

  /** Resolves the create result when the bind succeeded (for the one-time reveal), else null. */
  async function onCreate(input: CreateTriggerInput): Promise<AstroliftAgentTriggerResult | null> {
    try {
      const { data: res } = await createTrigger({ variables: { agentSlug, ...input } });
      const result = res?.createAgentTrigger;
      if (result?.ok) {
        toast.success(`Trigger bound for ${agentName}`);
        return result;
      }
      toast.error("Couldn't bind trigger", { description: result?.message ?? "Unknown error" });
      return null;
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e);
      toast.error("Couldn't bind trigger", { description: message });
      return null;
    }
  }

  /** Throws on failure so the confirm dialog surfaces the error and stays open. */
  async function onUnbind(slug: string): Promise<void> {
    const { data: res } = await unbindTrigger({ variables: { slug } });
    const result = res?.unbindAgentTrigger;
    if (!result?.ok) {
      throw new Error(result?.message ?? "Couldn't remove trigger");
    }
    toast.success("Trigger removed");
  }

  return {
    agentSlug,
    agentName,
    error: data ? null : (error ?? null),
    onRetry: () => {
      void refetch().catch(() => {});
    },
    triggers,
    // `!orgId` (the #1022 cold-load race) skips the query, so treat it as
    // pending too — otherwise the empty state flashes before it resolves.
    loading: loading || !orgId,
    creating,
    onCreate,
    onUnbind,
  };
}
