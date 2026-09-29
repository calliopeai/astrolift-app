"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import * as React from "react";
import { toast } from "sonner";

import { selectRows } from "@/components/list/select-rows";
import { useListState } from "@/components/list/use-list-state";
import type {
  DestroyAgentBoxMutation,
  EnsureAgentBoxMutation,
} from "@/graphql/__generated__/operations";
import { DESTROY_AGENT_BOX, ENSURE_AGENT_BOX } from "@/graphql/agents/agents.mutations";
import { LIST_AGENT_BOXES, LIST_AGENT_ENVIRONMENT_SPECS } from "@/graphql/agents/agents.queries";
import type {
  AgentBoxesData,
  AgentBoxesVars,
  AstroliftAgentBox,
  AstroliftAgentEnvironmentSpec,
} from "@/graphql/agents/agents.types";

import { AGENT_BOXES_LIST, AGENT_BOXES_SELECT } from "./agent-boxes-list";

export interface StartBoxInput {
  specSlug: string;
  name: string;
  /** Idle timeout in seconds, as the preset's string value. */
  idle: string;
}

/**
 * The org's agent boxes (URL list state, filtered, sorted and paged in the
 * client: see agent-boxes-list.ts; polled while one provisions), the
 * environment specs the new-box dialog offers, and the ensure / destroy
 * mutations. The data half of BoxesTabView.
 */
export function useAgentBoxes(orgId: string) {
  const [includeEnded, setIncludeEnded] = React.useState(false);
  const list = useListState(AGENT_BOXES_LIST);

  const query = useQuery<AgentBoxesData, AgentBoxesVars>(LIST_AGENT_BOXES, {
    variables: { orgId, includeEnded },
    // A box takes a moment to go provisioning -> running, so the button press
    // visibly resolves instead of leaving the operator to reload.
    pollInterval: 5000,
    skip: !orgId,
  });
  const data = query.data ?? query.previousData;
  const page = selectRows<AstroliftAgentBox>(
    data?.agentBoxes ?? [],
    {
      filters: list.filters,
      q: list.state.q,
      sort: list.state.sort,
      page: list.state.page,
      pageSize: list.state.pageSize,
    },
    AGENT_BOXES_SELECT
  );
  const refetch = (): void => {
    void query.refetch();
  };

  const { data: specData } = useQuery<{
    agentEnvironmentSpecs: AstroliftAgentEnvironmentSpec[];
  }>(LIST_AGENT_ENVIRONMENT_SPECS, { variables: { orgId }, skip: !orgId });
  const specs = specData?.agentEnvironmentSpecs ?? [];

  const [ensureBox, { loading: starting }] = useMutation<EnsureAgentBoxMutation>(ENSURE_AGENT_BOX);
  const [destroyBox] = useMutation<DestroyAgentBoxMutation>(DESTROY_AGENT_BOX);

  /** Resolves true when the box is starting, so the dialog can close. */
  async function startBox({ specSlug, name, idle }: StartBoxInput): Promise<boolean> {
    if (!specSlug) return false;
    try {
      const { data: resp } = await ensureBox({
        variables: {
          orgId,
          input: {
            environmentSpecSlug: specSlug,
            name: name.trim(),
            idleTimeoutSeconds: Number(idle),
          },
        },
      });
      const result = resp?.ensureAgentBox;
      if (!result?.ok) {
        throw new Error(result?.errors?.[0]?.message ?? "Could not start the box");
      }
      toast.success(`Box ${result.data?.slug ?? ""} is starting`);
      refetch();
      return true;
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error("Couldn't start the box", { description: message });
      return false;
    }
  }

  async function destroy(box: AstroliftAgentBox) {
    try {
      const { data: resp } = await destroyBox({ variables: { slug: box.slug } });
      const result = resp?.destroyAgentBox;
      if (!result?.ok) {
        throw new Error(result?.errors?.[0]?.message ?? "Could not destroy the box");
      }
      toast.success(`Destroyed ${box.slug}`);
      refetch();
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      toast.error("Couldn't destroy the box", { description: message });
    }
  }

  function copyAttach(line: string) {
    void navigator.clipboard.writeText(line);
    toast.success("Attach command copied");
  }

  return {
    list,
    rows: page.rows,
    totalCount: page.totalCount,
    loading: query.loading && !data,
    // Showing ended boxes asks a new question; the old answer fades until it lands.
    stale: !query.data && Boolean(query.previousData),
    error: query.error && !data ? { message: query.error.message } : null,
    onRetry: refetch,
    specs,
    starting,
    includeEnded,
    toggleIncludeEnded: () => setIncludeEnded((v) => !v),
    startBox,
    destroyBox: destroy,
    copyAttach,
  };
}
