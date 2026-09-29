"use client";

import { useMutation } from "@apollo/client/react";
import * as React from "react";
import { toast } from "sonner";

import { SET_ORGANIZATION_MODULE } from "@/graphql/identity/identity.mutations";
import type {
  AstroliftOrganizationModule,
  MutationResult,
} from "@/graphql/identity/identity.types";
import { useFeatureFlag } from "@/graphql/server/server.hooks";
import { useModules } from "@/graphql/user/user.hooks";
import { GET_ME } from "@/graphql/user/user.queries";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

// The per-org modules an org admin can turn on/off for their org (#1859).
// The install admin can still force one off for every org — checked below
// against astroliftServerInfo.featureFlags.
export const MODULE_CONFIG = [
  {
    key: "chat_studio_integration",
    label: "Chat Studio integration",
    description: "Lets Chat Studio reach this organization's apps through the builder API.",
    installFlagKey: "modules.chat_studio_integration_allowed",
  },
  {
    key: "agent_live_attach",
    label: "Agent live attach",
    description: "Lets members attach live to a running agent session.",
    installFlagKey: "modules.agent_live_attach_allowed",
  },
  {
    key: "chat_studio_agent_runs",
    label: "Chat Studio agent runs",
    description: "Lets Chat Studio launch this organization's registered agents.",
    installFlagKey: "modules.chat_studio_agent_runs_allowed",
  },
] as const;

export interface ModuleItem {
  key: string;
  label: string;
  description: string;
  enabled: boolean;
  /** Install-wide kill switch (#1859): false forces the module off for every org. */
  installAllowed: boolean;
}

/** The server half of ModulesCard: entitlements, the install gate and the toggle. */
export function useModulesCard() {
  const { modules, loading: modulesLoading } = useModules();
  const { can, loading: permsLoading } = useMyPermissions();
  const canManage = can("org.update");

  // One call per configured module; MODULE_CONFIG is a fixed-length const.
  const installAllowed = [
    useFeatureFlag(MODULE_CONFIG[0].installFlagKey),
    useFeatureFlag(MODULE_CONFIG[1].installFlagKey),
    useFeatureFlag(MODULE_CONFIG[2].installFlagKey),
  ];

  const [setModule] = useMutation<{
    setOrganizationModule: MutationResult<AstroliftOrganizationModule>;
  }>(SET_ORGANIZATION_MODULE, {
    refetchQueries: [{ query: GET_ME }],
    awaitRefetchQueries: true,
  });

  const [pendingKey, setPendingKey] = React.useState<string | null>(null);

  async function onToggle(key: string, next: boolean) {
    const config = MODULE_CONFIG.find((m) => m.key === key);
    if (!config) return;
    setPendingKey(config.key);
    try {
      const { data } = await setModule({
        variables: { input: { key: config.key, enabled: next } },
      });
      const result = data?.setOrganizationModule;
      if (!result?.ok) {
        throw new Error(result?.errors?.[0]?.message ?? "Could not update the module.");
      }
      toast.success(`${config.label} ${next ? "enabled" : "disabled"}`);
    } catch (err) {
      const message =
        err instanceof Error && err.message ? err.message : "Could not update the module.";
      toast.error(message);
    } finally {
      setPendingKey(null);
    }
  }

  const items: ModuleItem[] = MODULE_CONFIG.map((config, i) => ({
    key: config.key,
    label: config.label,
    description: config.description,
    enabled: modules.get(config.key)?.enabled ?? false,
    installAllowed: installAllowed[i],
  }));

  return {
    items,
    loading: modulesLoading && modules.size === 0,
    canManage,
    permsLoading,
    pendingKey,
    onToggle,
  };
}
