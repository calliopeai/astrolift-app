"use client";

import { Section } from "@/components/ui/section";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

import type { ModuleItem, useModulesCard } from "./use-modules-card";

export type ModulesCardProps = ReturnType<typeof useModulesCard>;

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
  item,
  canManage,
  permsLoading,
  pending,
  onToggle,
}: {
  item: ModuleItem;
  canManage: boolean;
  permsLoading: boolean;
  pending: boolean;
  onToggle: (next: boolean) => void;
}) {
  // Install-wide kill switch (#1859) — off forces the module off for every
  // organization, regardless of what this org's own row says.
  const { installAllowed, enabled } = item;
  const disabled = pending || permsLoading || !canManage || !installAllowed;

  return (
    <div className="border-border/60 flex items-center gap-4 border-b py-3 last:border-b-0">
      <div className="min-w-0 flex-1">
        <p className="font-medium [overflow-wrap:anywhere]">{item.label}</p>
        <p className="text-muted-foreground mt-0.5 text-sm [overflow-wrap:anywhere]">
          {item.description}
        </p>
        {!installAllowed && (
          <p className="text-muted-foreground mt-1 text-xs">Turned off on this install</p>
        )}
      </div>
      <ModuleToggle
        checked={enabled}
        disabled={disabled}
        onToggle={() => onToggle(!enabled)}
        label={`${enabled ? "Disable" : "Enable"} ${item.label}`}
      />
    </div>
  );
}

export function ModulesCard({
  items,
  loading,
  canManage,
  permsLoading,
  pendingKey,
  onToggle,
}: ModulesCardProps) {
  return (
    <Section
      title="Modules"
      description="Optional integrations for this organization. An install admin can still force one off for every organization."
      divided
    >
      <div className="flex min-w-0 flex-col">
        {loading ? (
          <div className="flex flex-col gap-3 py-2">
            {items.map((m) => (
              <div key={m.key} className="flex min-w-0 items-center gap-4">
                <div className="min-w-0 flex-1 space-y-2">
                  <Skeleton className="h-4 w-48 max-w-full" />
                  <Skeleton className="h-3 w-72 max-w-full" />
                </div>
                <Skeleton className="h-5 w-9 rounded-full" />
              </div>
            ))}
          </div>
        ) : (
          items.map((item) => (
            <ModuleRow
              key={item.key}
              item={item}
              canManage={canManage}
              permsLoading={permsLoading}
              pending={pendingKey === item.key}
              onToggle={(next) => onToggle(item.key, next)}
            />
          ))
        )}
      </div>
    </Section>
  );
}
