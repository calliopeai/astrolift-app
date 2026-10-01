"use client";

import { AlertTriangleIcon, ChevronRightIcon } from "lucide-react";
import * as React from "react";
import { useTranslations } from "next-intl";
import { presetLabel, scopePresentation } from "./scope-presentation";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Skeleton } from "@/components/ui/skeleton";
import type { AstroliftApiTokenScopeCatalog } from "@/graphql/__generated__/schema";
import { cn } from "@/lib/utils";

import type { useScopeCatalog } from "./use-scope-catalog";

const SURFACE_TITLES: Record<string, string> = {
  apps: "Apps",
  clusters: "Clusters",
  secrets: "Secrets",
  agents: "Agents",
  workflows: "Workflows",
  administration: "Administration",
};

type Catalog = AstroliftApiTokenScopeCatalog;
type Scope = Catalog["scopes"][number];

export type ScopePickerProps = ReturnType<typeof useScopeCatalog> & {
  value: string[];
  onChange: (next: string[]) => void;
};

/**
 * Pick a token's scopes from the server's catalog (#2120).
 *
 * Grouped by surface, each scope saying what it unlocks, so an operator can
 * choose the narrow one instead of reaching for `admin`. Scopes only ever
 * narrow the owner's roles: one the caller's roles can never exercise is shown
 * disabled with the reason.
 */
export function ScopePicker({ catalog, loading, error, value, onChange }: ScopePickerProps) {
  const t = useTranslations("apiKeys");
  if (loading && !catalog) {
    return <Skeleton className="h-48 w-full" />;
  }
  if (error || !catalog) {
    return (
      <p className="text-destructive text-sm">
        {t("picker.loadFailed")}
        {error?.message ? (
          <span className="block [overflow-wrap:anywhere]">{error.message}</span>
        ) : null}
      </p>
    );
  }

  const selected = new Set(value);
  const toggle = (scope: string) =>
    onChange(selected.has(scope) ? value.filter((s) => s !== scope) : [...value, scope]);
  const bySurface = new Map<string, Scope[]>();
  for (const scope of catalog.scopes) {
    bySurface.set(scope.surface, [...(bySurface.get(scope.surface) ?? []), scope]);
  }
  const usable = new Set(catalog.scopes.filter((s) => s.available).map((s) => s.value));

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-muted-foreground text-xs">{t("picker.startFrom")}</span>
        {catalog.presets.map((preset) => (
          <Button
            key={preset.key}
            type="button"
            size="sm"
            variant="outline"
            onClick={() => onChange(preset.scopes.filter((s) => usable.has(s)))}
          >
            {presetLabel(preset.key, preset.label, t)}
          </Button>
        ))}
      </div>

      {selected.has("admin") && (
        <div
          role="alert"
          className="border-warning-border bg-warning-bg text-warning-fg flex gap-2 rounded-md border p-3 text-xs"
        >
          <AlertTriangleIcon className="mt-0.5 size-4 shrink-0" />
          <p>{t("picker.adminWarning")}</p>
        </div>
      )}

      {[...bySurface.entries()].map(([surface, scopes]) => (
        <fieldset key={surface} className="min-w-0 space-y-1.5">
          <legend className="text-muted-foreground mb-1 text-xs font-medium tracking-wide uppercase">
            {Object.hasOwn(SURFACE_TITLES, surface) ? t(`picker.surfaces.${surface}`) : surface}
          </legend>
          {scopes.map((scope) => (
            <ScopeRow
              key={scope.value}
              scope={scope}
              checked={selected.has(scope.value)}
              onToggle={() => toggle(scope.value)}
            />
          ))}
        </fieldset>
      ))}
    </div>
  );
}

function ScopeRow({
  scope,
  checked,
  onToggle,
}: {
  scope: Scope;
  checked: boolean;
  onToggle: () => void;
}) {
  const t = useTranslations("apiKeys");
  const presentation = scopePresentation(scope, t);
  const id = `scope-${scope.value.replace(/[^a-z0-9]/gi, "-")}`;
  // A scope already selected stays removable even when it is not usable, so a
  // preset or an older default can always be undone.
  const disabled = !scope.available && !checked;
  return (
    <div
      className={cn(
        "flex min-w-0 gap-3 rounded-md border p-2.5",
        checked ? "border-primary/60 bg-primary/5" : "border-input",
        disabled && "opacity-60"
      )}
    >
      <Checkbox
        id={id}
        checked={checked}
        disabled={disabled}
        onCheckedChange={onToggle}
        className="mt-0.5"
      />
      <div className="min-w-0 flex-1 space-y-1">
        <label htmlFor={id} className="flex flex-wrap items-baseline gap-x-2 text-sm font-medium">
          {presentation.label}
          <code className="text-muted-foreground font-mono text-xs font-normal">{scope.value}</code>
          {scope.sensitive && (
            <span className="text-warning-fg text-xs font-normal">{t("picker.sensitive")}</span>
          )}
        </label>
        <p className="text-muted-foreground text-xs [overflow-wrap:anywhere]">
          {presentation.description}
        </p>
        {!scope.available && (
          <p className="text-muted-foreground text-xs italic">{presentation.unavailableReason}</p>
        )}
        {scope.permissions.length > 0 && scope.value !== "admin" && (
          <Collapsible>
            <CollapsibleTrigger className="text-muted-foreground hover:text-foreground group flex items-center gap-1 text-xs">
              <ChevronRightIcon className="size-3 transition group-data-[state=open]:rotate-90" />
              {t("picker.unlocks", { count: scope.permissions.length })}
            </CollapsibleTrigger>
            <CollapsibleContent>
              <ul className="mt-1 flex flex-wrap gap-1">
                {scope.permissions.map((p) => (
                  <li key={p} className="bg-muted text-2xs rounded px-1.5 py-0.5 font-mono">
                    {p}
                  </li>
                ))}
              </ul>
            </CollapsibleContent>
          </Collapsible>
        )}
      </div>
    </div>
  );
}
