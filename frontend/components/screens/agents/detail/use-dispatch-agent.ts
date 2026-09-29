"use client";

import { useMutation } from "@apollo/client/react";
import { toast } from "sonner";

import { RUN_AGENT } from "@/graphql/agents/agents.mutations";
import type { AstroliftAgentListItem } from "@/graphql/agents/agents.types";

interface RunAgentResp {
  runAstroliftAgent: {
    ok: boolean;
    errors: { code: string; message: string; field: string | null }[];
    data: { id: string; status: string; createdAt: string } | null;
  };
}

/**
 * Dispatch one run of an agent (`runAstroliftAgent`, behind `agent.dispatch`
 * on the server) and report the outcome as a toast. Resolves the new run's
 * id, or null when it did not start. `onDispatched` runs after a start, before
 * the promise settles: the caller refetches its runs there.
 */
export function useDispatchAgent(
  agent: Pick<AstroliftAgentListItem, "slug" | "name">,
  onDispatched?: (runId: string) => Promise<unknown> | void
) {
  const [runAgent, { loading: dispatching }] = useMutation<RunAgentResp>(RUN_AGENT);

  async function dispatch(): Promise<string | null> {
    try {
      const { data: res } = await runAgent({ variables: { input: { agentSlug: agent.slug } } });
      const result = res?.runAstroliftAgent;
      if (!result?.ok) throw new Error(result?.errors?.[0]?.message ?? "Dispatch failed");
      toast.success(`Dispatched ${agent.name}`);
      const id = result.data?.id ?? "";
      await onDispatched?.(id);
      return id;
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error(`Couldn't dispatch ${agent.name}`, { description: message });
      return null;
    }
  }

  return { dispatch, dispatching };
}
