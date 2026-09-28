import type { AstroliftAgentListItem, AstroliftAgentRunSpec } from "@/graphql/agents/agents.types";

import type { AgentControlScreenProps } from "./AgentControl";
import type { SaveRunSpecResult } from "./use-agent-control";

/** Hand-typed fixtures for the agent Control tab (run-spec editor). */

export const CONTROL_LONG =
  "nightly-customer-support-ticket-triage-and-escalation-agent-for-the-emea-and-apac-regions-with-a-long-name";

export const CONTROL_AGENT: AstroliftAgentListItem = {
  id: "a9e1c3d2-0000-4000-8000-000000000011",
  name: "Ticket Triage",
  slug: "ticket-triage",
  appSlug: "support-agents",
  projectSlug: "support",
  sourceRepo: "acme/support-agents",
  sourceUrl: "https://github.com/acme/support-agents",
  runFamily: "task",
  runMode: "once",
  runPaused: false,
  runCronExpression: "",
  lastRunStatus: "succeeded",
  lastRunAt: "2026-09-27T14:05:41Z",
  runningCount: 0,
};

export const PERSISTED_SERVICE_SPEC: AstroliftAgentRunSpec = {
  id: CONTROL_AGENT.id,
  slug: CONTROL_AGENT.slug,
  kind: "agent",
  runFamily: "service",
  runMode: "once",
  runCronExpression: "",
  runPaused: false,
  runMaxParallel: null,
  replicas: 4,
  scheduledScaleTo: 6,
  scaleUpCron: "0 8 * * 1-5",
  scaleDownCron: "0 18 * * 1-5",
};

const saved =
  (persisted: AstroliftAgentRunSpec | null) => async (): Promise<SaveRunSpecResult> => ({
    ok: true,
    persisted,
  });

/** A save that the server rejects with an error naming `field`. */
export const rejectField =
  (field: string, message: string) => async (): Promise<SaveRunSpecResult> => ({
    ok: false,
    fieldError: { field, message },
  });

/** A save that never settles, so the button stays in its saving state. */
export const pendingSave = () => new Promise<SaveRunSpecResult>(() => {});

export const CONTROL: Omit<AgentControlScreenProps, "triggerEditor"> = {
  agent: CONTROL_AGENT,
  saving: false,
  onSave: saved(null),
};

export const CONTROL_SAVES_SERVICE: Omit<AgentControlScreenProps, "triggerEditor"> = {
  ...CONTROL,
  agent: { ...CONTROL_AGENT, runFamily: "service" },
  onSave: saved(PERSISTED_SERVICE_SPEC),
};
