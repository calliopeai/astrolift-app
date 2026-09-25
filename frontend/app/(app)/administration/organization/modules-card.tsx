"use client";

import { useMutation } from "@apollo/client/react";
import * as React from "react";
import { toast } from "sonner";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { SET_ORGANIZATION_MODULE } from "@/graphql/identity/identity.mutations";
import type {
  AstroliftOrganizationModule,
  MutationResult,
} from "@/graphql/identity/identity.types";
import { useFeatureFlag } from "@/graphql/server/server.hooks";
import { useModules } from "@/graphql/user/user.hooks";
import { GET_ME } from "@/graphql/user/user.queries";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";
import { cn } from "@/lib/utils";

// The per-org modules an org admin can turn on/off for their org (#1859).
// The install admin can still force one off for every org — checked below
// against astroliftServerInfo.featureFlags.
const MODULE_CONFIG = [
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
] as const;

/**
 * Accessible on/off switch. Same pattern as the cluster settings auth gate
 * toggle — the repo has no shadcn/radix Switch primitive, so this is a
 * small local control styled with the semantic status tokens rather than
 * a new shared component.
 */
function ModuleToggle({
  checked,
  disabled,
  onToggle,
  label,
}: {
  checked: boolean;
  disabled?: boolean;
  onToggle: () => void;
  label: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={onToggle}
      className={cn(
        "relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full transition-colors",
        "focus-visible:ring-ring focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:outline-none",
        "disabled:cursor-not-allowed disabled:opacity-50",
        checked ? "bg-success" : "bg-muted-foreground/30"
      )}
    >
      <span
        className={cn(
          "inline-block size-4 rounded-full bg-white shadow transition-transform",
          checked ? "translate-x-4" : "translate-x-0.5"
        )}
      />
    </button>
  );
}

function ModuleRow({
  config,
  enabled,
  canManage,
  permsLoading,
  pending,
  onToggle,
}: {
  config: (typeof MODULE_CONFIG)[number];
  enabled: boolean;
  canManage: boolean;
  permsLoading: boolean;
  pending: boolean;
  onToggle: (next: boolean) => void;
}) {
  // Install-wide kill switch (#1859) — off forces the module off for every
  // organization, regardless of what this org's own row says.
  const installAllowed = useFeatureFlag(config.installFlagKey);
  const disabled = pending || permsLoading || !canManage || !installAllowed;

  return (
    <div className="border-border/60 flex items-center gap-4 border-b py-3 last:border-b-0">
      <div className="min-w-0 flex-1">
        <p className="font-medium">{config.label}</p>
        <p className="text-muted-foreground mt-0.5 text-sm">{config.description}</p>
        {!installAllowed && (
          <p className="text-muted-foreground mt-1 text-xs">Turned off on this install</p>
        )}
      </div>
      <ModuleToggle
        checked={enabled}
        disabled={disabled}
        onToggle={() => onToggle(!enabled)}
        label={`${enabled ? "Disable" : "Enable"} ${config.label}`}
      />
    </div>
  );
}

export function ModulesCard() {
  const { modules, loading: modulesLoading } = useModules();
  const { can, loading: permsLoading } = useMyPermissions();
  const canManage = can("org.update");

  const [setModule] = useMutation<{
    setOrganizationModule: MutationResult<AstroliftOrganizationModule>;
  }>(SET_ORGANIZATION_MODULE, {
    refetchQueries: [{ query: GET_ME }],
    awaitRefetchQueries: true,
  });

  const [pendingKey, setPendingKey] = React.useState<string | null>(null);

  async function handleToggle(config: (typeof MODULE_CONFIG)[number], next: boolean) {
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

  const loading = modulesLoading && modules.size === 0;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Modules</CardTitle>
        <CardDescription>
          Optional integrations for this organization. An install admin can still force one off for
          every organization.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col">
        {loading ? (
          <div className="flex flex-col gap-3 py-2">
            {MODULE_CONFIG.map((m) => (
              <div key={m.key} className="flex items-center gap-4">
                <div className="flex-1 space-y-2">
                  <Skeleton className="h-4 w-48" />
                  <Skeleton className="h-3 w-72" />
                </div>
                <Skeleton className="h-5 w-9 rounded-full" />
              </div>
            ))}
          </div>
        ) : (
          MODULE_CONFIG.map((config) => (
            <ModuleRow
              key={config.key}
              config={config}
              enabled={modules.get(config.key)?.enabled ?? false}
              canManage={canManage}
              permsLoading={permsLoading}
              pending={pendingKey === config.key}
              onToggle={(next) => handleToggle(config, next)}
            />
          ))
        )}
      </CardContent>
    </Card>
  );
}
