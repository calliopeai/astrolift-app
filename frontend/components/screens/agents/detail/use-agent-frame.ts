"use client";

import { useApolloClient, useQuery } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import { usePathname, useRouter } from "next/navigation";
import * as React from "react";
import { toast } from "sonner";

import {
  LIST_AGENT_ENVIRONMENT_SPECS,
  LIST_AGENT_FLEET,
  LIST_AGENT_TASKS,
} from "@/graphql/agents/agents.queries";
import type {
  AstroliftAgentEnvironmentSpec,
  AstroliftAgentListItem,
} from "@/graphql/agents/agents.types";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import { LIST_ENVIRONMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import { type PermissionCheck, useMyPermissions } from "@/lib/permissions/use-my-permissions";

import type { AgentFrameProps } from "./AgentFrame";
import { agentTabHref, resolveAgentTab } from "./agent-tabs-model";
import { useDispatchAgent } from "./use-dispatch-agent";

interface FleetResp {
  agentFleet: AstroliftAgentListItem[];
}
interface EnvsResp {
  astroliftEnvironments: { id: string; name: string; clusterSlug?: string | null }[];
}
interface SpecsResp {
  agentEnvironmentSpecs: AstroliftAgentEnvironmentSpec[];
}
interface TasksResp {
  agentTasks: { id: string; createdAt: string; failureMessage?: string | null }[];
}

const FAILED = new Set(["failed", "timed_out", "error"]);

/**
 * The data half of AgentFrame, and the agent every tab reads (the frame
 * hands it down once found).
 *
 * There is no `agent(slug)` query that carries the fleet row's run state, so
 * the agent resolves against the org's `agentFleet`, matched on slug, as the
 * detail shell did. The header's context reads the same queries the tabs do,
 * so Apollo serves them once: the agent's environments (its cluster), and
 * the org's environment specs (whether it uses the managed model; the spec
 * slug is the agent slug). The latest run is read only when the last run
 * failed, for its reason, from the same `agentTasks` query the Runs tab
 * polls. Run now is `agent.dispatch`, the grant the server checks.
 */
export function useAgentFrame(slug: string): {
  frame: Omit<AgentFrameProps, "children">;
  agent: AstroliftAgentListItem | null;
  orgId: string;
} {
  const t = useTranslations("agentFrame");
  const router = useRouter();
  const pathname = usePathname() ?? "";
  const client = useApolloClient();
  const { org } = useActiveOrg();
  const orgId = org?.id ?? "";
  const perms = useMyPermissions();
  // Optimistic while the permission set loads, as `Can` is; the backend
  // still enforces it.
  const allow = (p: PermissionCheck) => (perms.loading && perms.granted.size === 0) || perms.can(p);

  const fleetQ = useQuery<FleetResp>(LIST_AGENT_FLEET, {
    variables: { orgId },
    skip: !orgId,
    fetchPolicy: "cache-and-network",
  });
  const agent = React.useMemo(
    () => fleetQ.data?.agentFleet?.find((a) => a.slug === slug) ?? null,
    [fleetQ.data?.agentFleet, slug]
  );

  const envsQ = useQuery<EnvsResp>(LIST_ENVIRONMENTS, {
    variables: { appSlug: agent?.appSlug },
    skip: !agent,
  });
  const specsQ = useQuery<SpecsResp>(LIST_AGENT_ENVIRONMENT_SPECS, {
    variables: { orgId },
    skip: !orgId || !agent,
    fetchPolicy: "cache-and-network",
  });
  const failing = Boolean(agent?.lastRunStatus && FAILED.has(agent.lastRunStatus.toLowerCase()));
  const tasksQ = useQuery<TasksResp>(LIST_AGENT_TASKS, {
    variables: { orgId, status: null, workloadId: agent?.id },
    skip: !orgId || !failing,
  });

  const { dispatch, dispatching } = useDispatchAgent(agent ?? { slug, name: slug }, async () => {
    await client.refetchQueries({ include: [LIST_AGENT_TASKS, LIST_AGENT_FLEET] });
  });

  async function onRun(): Promise<boolean> {
    const id = await dispatch();
    if (id === null) return false;
    if (resolveAgentTab(pathname, slug) !== "runs") router.push(agentTabHref(slug, "runs"));
    return true;
  }

  function onCopyId() {
    if (!agent) return;
    void (async () => {
      try {
        await navigator.clipboard.writeText(agent.id);
        toast.success(t("idCopied"));
      } catch {
        toast.error(t("copyFailed"));
      }
    })();
  }

  const spec = specsQ.data?.agentEnvironmentSpecs?.find((s) => s.slug === slug) ?? null;
  const cluster = envsQ.data?.astroliftEnvironments?.find((e) => e.clusterSlug)?.clusterSlug;
  const latest = [...(tasksQ.data?.agentTasks ?? [])].sort((a, b) =>
    (b.createdAt ?? "").localeCompare(a.createdAt ?? "")
  )[0];

  return {
    agent,
    orgId,
    frame: {
      slug,
      pathname,
      agent,
      loading: !orgId || (fleetQ.loading && !fleetQ.data),
      error: fleetQ.error && !fleetQ.data ? fleetQ.error.message : null,
      onRetry: () => void fleetQ.refetch(),
      model: spec ? (spec.managedModel ? t("managedModel") : t("apiKey")) : null,
      clusterSlug: cluster ?? null,
      failedRun: failing ? { id: latest?.id ?? "", reason: latest?.failureMessage ?? null } : null,
      canRun: allow("agent.dispatch"),
      dispatching,
      onRun,
      onCopyId,
    },
  };
}
