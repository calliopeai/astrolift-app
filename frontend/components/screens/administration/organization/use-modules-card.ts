"use client";

import { useMutation } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import { SET_ORGANIZATION_MODULE } from "@/graphql/identity/identity.mutations";
import type {
  AstroliftOrganizationModule,
  MutationResult,
} from "@/graphql/identity/identity.types";
import { useModules } from "@/graphql/user/user.hooks";
import { GET_ME } from "@/graphql/user/user.queries";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

// The per-org modules an org admin can turn on/off for their org (#1859).
// Only the ones this install offers are listed: me.modules[].available says
// which (#2551), so an install admin's switch or the licence keeps one out.
export const MODULE_CONFIG = [
  {
    key: "chat_studio_integration",
    label: "Chat Studio integration",
    description: "Lets Chat Studio ship apps to this organization through the builder API.",
  },
  {
    key: "agent_live_attach",
    label: "Agent live attach",
    description: "Lets members attach live to a running agent session.",
  },
  {
    key: "chat_studio_agent_runs",
    label: "Chat Studio agent runs",
    description: "Lets Chat Studio launch this organization's registered agents.",
  },
  {
    key: "agent_policy_enforcement",
    label: "Agent policy enforcement",
    description: "Lets Zentinelle apply policy actions to this organization's agents and boxes.",
  },
] as const;

export interface ModuleItem {
  key: string;
  label: string;
  description: string;
  enabled: boolean;
  /** Who last switched it: "org_admin", "licence" or null when never switched. */
  source: string | null;
}

// Refusal codes from setOrganizationModule the card has its own words for.
const REFUSAL_CODES = ["MODULE_DISABLED_BY_INSTALL", "MODULE_NOT_LICENSED"] as const;

/** The server half of ModulesCard: the modules this install offers and the toggle. */
export function useModulesCard() {
  const t = useTranslations("adminModules");
  const { modules, loading, error, refetch, hasData } = useModules();
  const { can, loading: permsLoading } = useMyPermissions();
  const canManage = can("org.update");

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
        const code = result?.errors?.[0]?.code;
        const known = REFUSAL_CODES.find((c) => c === code);
        throw new Error(
          known ? t(`errors.${known}`) : (result?.errors?.[0]?.message ?? t("updateError"))
        );
      }
      toast.success(
        t(next ? "enabledToast" : "disabledToast", { module: t(`items.${config.key}.label`) })
      );
    } catch (err) {
      const message = err instanceof Error && err.message ? err.message : t("updateError");
      toast.error(message);
    } finally {
      setPendingKey(null);
    }
  }

  const items: ModuleItem[] = MODULE_CONFIG.filter(
    (config) => modules.get(config.key)?.available
  ).map((config) => ({
    key: config.key,
    label: t(`items.${config.key}.label`),
    description: t(`items.${config.key}.description`),
    enabled: modules.get(config.key)?.enabled ?? false,
    source: modules.get(config.key)?.source ?? null,
  }));

  return {
    items,
    loading: loading && !hasData,
    error: hasData ? null : (error ?? null),
    onRetry: () => {
      void refetch().catch(() => {});
    },
    canManage,
    permsLoading,
    pendingKey,
    onToggle,
  };
}
