import type { AstroliftApiTokenScopeCatalog } from "@/graphql/__generated__/schema";

/** Exact reviewed stock copy, not a replacement catalog or permission decision.
 * New/changed server metadata stays literal until its presentation is reviewed. */
export const STOCK_SCOPE_COPY: Record<string, { label: string; description: string; key: string }> =
  {
    "read:apps": {
      label: "Read apps",
      description: "List apps, deployments, environments and secret names.",
      key: "scope0",
    },
    "write:apps": {
      label: "Write apps",
      description: "Deploy, edit environment variables and manage app config.",
      key: "scope1",
    },
    "app:onboard": {
      label: "Onboard apps",
      description:
        "Register apps and run CI setup (webhook, secrets, workflow), without deploy or delete.",
      key: "scope2",
    },
    "read:clusters": {
      label: "Read clusters",
      description: "List clusters and read provider state.",
      key: "scope3",
    },
    "write:clusters": {
      label: "Update clusters",
      description: "Change a cluster's settings: ingress class, auth gate, region, endpoint.",
      key: "scope4",
    },
    "manage:clusters": {
      label: "Operate clusters",
      description:
        "Run a cluster's recipe install, refresh its management state, reconcile its ingresses.",
      key: "scope5",
    },
    "manage:auth-users": {
      label: "Manage sign-in users",
      description:
        "Create, disable, delete and reset the users of a cluster's central auth, and their groups.",
      key: "scope6",
    },
    "write:app-access": {
      label: "Set app access",
      description: "Choose which users and groups may enter an app behind central auth.",
      key: "scope7",
    },
    "secret:read": {
      label: "Reveal secrets",
      description: "Reveal stored secret values. Grant only when required.",
      key: "scope8",
    },
    "secret:write": {
      label: "Write secrets",
      description: "Set, rotate and delete secrets without revealing them.",
      key: "scope9",
    },
    "mcp:read": {
      label: "MCP read",
      description: "List agent packages and inspect runs through remote MCP.",
      key: "scope10",
    },
    "mcp:dispatch": {
      label: "MCP dispatch",
      description:
        "Run, steer and hard-stop agents; attach to agent boxes, subject to account permissions.",
      key: "scope11",
    },
    "mcp:write": {
      label: "MCP write",
      description: "Sync agent repositories and package definitions.",
      key: "scope12",
    },
    "agent-env-spec:write": {
      label: "Write agent environments",
      description: "Create, update and delete agent environment specs.",
      key: "scope13",
    },
    "workflow:write": {
      label: "Write workflows",
      description: "Create, update and delete workflows.",
      key: "scope14",
    },
    "workflow:trigger": {
      label: "Run workflows",
      description: "Start workflow runs, without editing them.",
      key: "scope15",
    },
    "project:write": {
      label: "Write projects",
      description: "Create, update and delete projects.",
      key: "scope16",
    },
    "team:write": {
      label: "Write teams",
      description: "Create, update and delete teams.",
      key: "scope17",
    },
    admin: {
      label: "Admin",
      description:
        "Everything the owner can do, now and as new permissions are added. Prefer the narrow scopes.",
      key: "scope18",
    },
  };
const STOCK_PRESET_LABELS: Record<string, string> = {
  read_only: "Read-only",
  cli: "CLI default",
  ci_deploy: "CI deploy",
  cluster_operator: "Cluster operator",
};
export function scopePresentation(
  scope: AstroliftApiTokenScopeCatalog["scopes"][number],
  t: (key: string) => string
) {
  const stock = Object.hasOwn(STOCK_SCOPE_COPY, scope.value)
    ? STOCK_SCOPE_COPY[scope.value]
    : undefined;
  return {
    label: stock && scope.label === stock.label ? t(`catalog.${stock.key}.label`) : scope.label,
    description:
      stock && scope.description === stock.description
        ? t(`catalog.${stock.key}.description`)
        : scope.description,
    unavailableReason:
      stock && scope.unavailableReason === "Your roles grant none of what this scope unlocks."
        ? t("picker.unavailable")
        : scope.unavailableReason,
  };
}
export function presetLabel(key: string, label: string, t: (key: string) => string) {
  return STOCK_PRESET_LABELS[key] === label ? t(`picker.presets.${key}`) : label;
}
