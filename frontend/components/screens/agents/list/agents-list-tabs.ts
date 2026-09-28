/** The Agents fleet page's tabs, in strip order, and their labels. */
export type AgentTab =
  | "active"
  | "boxes"
  | "dispatch"
  | "history"
  | "registry"
  | "theatre"
  | "metrics"
  | "logs"
  | "activity"
  | "reasoning"
  | "token-usage"
  | "compliance";

export const AGENT_TABS: readonly AgentTab[] = [
  "active",
  "boxes",
  "dispatch",
  "history",
  "registry",
  "theatre",
  "metrics",
  "logs",
  "activity",
  "reasoning",
  "token-usage",
  "compliance",
];

export const TAB_LABELS: Record<AgentTab, string> = {
  active: "Active",
  boxes: "Boxes",
  dispatch: "Dispatch",
  history: "History",
  registry: "Registry",
  theatre: "Theatre",
  metrics: "Metrics",
  logs: "Logs",
  activity: "Activity",
  reasoning: "Reasoning Traces",
  "token-usage": "Token Usage",
  compliance: "Compliance",
};

export const ZENTINELLE_TABS = new Set<AgentTab>([
  "activity",
  "reasoning",
  "token-usage",
  "compliance",
]);
