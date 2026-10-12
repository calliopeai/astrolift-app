"use client";

/**
 * Shared pickers for the workflow builder (spec 40).
 *
 *  - **AgentWorkloadPicker** — single-select Combobox over the org's
 *    registered agent workloads (`agentWorkloads(orgId)`); the selected
 *    id feeds a stage's `agentDefinitionGuid`.
 *  - **SkillRefsPicker** — multi-select chips Combobox over the org's
 *    skills union platform-global skills (`skills(orgId, isGlobal)`);
 *    the selected slugs feed a stage's `skillRefs`.
 *
 * Both are controlled and pure: `{ value, onChange, orgScoped }` plus the
 * options, which {@link useAgentWorkloadOptions} and {@link useSkillOptions}
 * fetch. `orgScoped` is the org GUID the listings are scoped to (`null`
 * while the org context is still resolving — the picker renders disabled).
 */

import * as React from "react";
import { useTranslations } from "next-intl";

import {
  Combobox,
  ComboboxChip,
  ComboboxChips,
  ComboboxChipsInput,
  ComboboxContent,
  ComboboxEmpty,
  ComboboxInput,
  ComboboxItem,
  ComboboxList,
  ComboboxValue,
  useComboboxAnchor,
} from "@/components/ui/combobox";
import type { AstroliftAgentListItem, AstroliftSkill } from "@/graphql/agents/agents.types";

// ─── AgentWorkloadPicker ─────────────────────────────────────────────────

export interface AgentWorkloadPickerProps {
  /** Selected agent workload GUID (stage `agentDefinitionGuid`), or null. */
  value: string | null;
  onChange: (workloadId: string | null) => void;
  /** Org GUID the listing is scoped to; null renders the picker disabled. */
  orgScoped: string | null;
  /** The org's agent workloads, sorted by name. */
  workloads: AstroliftAgentListItem[];
  loading: boolean;
  disabled?: boolean;
}

export function AgentWorkloadPicker({
  value,
  onChange,
  orgScoped,
  workloads,
  loading,
  disabled = false,
}: AgentWorkloadPickerProps) {
  const t = useTranslations("workflowPickers");
  const selected = workloads.find((w) => w.id === value) ?? null;

  return (
    <Combobox<AstroliftAgentListItem>
      items={workloads}
      itemToStringLabel={(w) => w.name}
      isItemEqualToValue={(a, b) => a.id === b.id}
      value={selected}
      onValueChange={(v) => onChange(v?.id ?? null)}
      disabled={disabled || !orgScoped}
    >
      <ComboboxInput
        placeholder={loading ? t("loadingAgents") : t("selectAgent")}
        disabled={disabled || !orgScoped}
        showClear
        aria-label={t("agentWorkload")}
      />
      <ComboboxContent>
        <ComboboxEmpty>{t("noAgents")}</ComboboxEmpty>
        <ComboboxList>
          {(item: AstroliftAgentListItem) => (
            <ComboboxItem key={item.id} value={item}>
              <div className="flex min-w-0 flex-1 items-center justify-between gap-2">
                <span className="truncate text-sm">{item.name}</span>
                <span className="text-muted-foreground truncate font-mono text-xs">
                  {item.appSlug}
                </span>
              </div>
            </ComboboxItem>
          )}
        </ComboboxList>
      </ComboboxContent>
    </Combobox>
  );
}

// ─── SkillRefsPicker ─────────────────────────────────────────────────────

// The chip/list option — a lightweight facade over AstroliftSkill so refs
// not present in the listing (e.g. from an imported manifest) still render.
export type SkillOption = Pick<AstroliftSkill, "slug" | "name" | "isGlobal">;

export interface SkillRefsPickerProps {
  /** Selected skill slugs (stage `skillRefs`). */
  value: string[];
  onChange: (skillRefs: string[]) => void;
  /** Org GUID the listing is scoped to; null renders the picker disabled. */
  orgScoped: string | null;
  /** Org skills union platform-global skills, sorted by name. */
  options: SkillOption[];
  loading: boolean;
  disabled?: boolean;
}

export function SkillRefsPicker({
  value,
  onChange,
  orgScoped,
  options,
  loading,
  disabled = false,
}: SkillRefsPickerProps) {
  const t = useTranslations("workflowPickers");
  const anchor = useComboboxAnchor();

  const selected = React.useMemo<SkillOption[]>(
    () =>
      value.map(
        (slug) => options.find((o) => o.slug === slug) ?? { slug, name: slug, isGlobal: false }
      ),
    [value, options]
  );

  return (
    <Combobox<SkillOption, true>
      multiple
      items={options}
      itemToStringLabel={(s) => s.name}
      isItemEqualToValue={(a, b) => a.slug === b.slug}
      value={selected}
      onValueChange={(v) => onChange(v.map((s) => s.slug))}
      disabled={disabled || !orgScoped}
    >
      <ComboboxChips ref={anchor} aria-label={t("skillRefs")}>
        <ComboboxValue>
          {(vals: SkillOption[]) => (
            <React.Fragment>
              {vals.map((s) => (
                <ComboboxChip key={s.slug} aria-label={s.name}>
                  {s.slug}
                </ComboboxChip>
              ))}
              <ComboboxChipsInput
                placeholder={vals.length > 0 ? "" : loading ? t("loadingSkills") : t("addSkills")}
                disabled={disabled || !orgScoped}
              />
            </React.Fragment>
          )}
        </ComboboxValue>
      </ComboboxChips>
      <ComboboxContent anchor={anchor}>
        <ComboboxEmpty>{t("noSkills")}</ComboboxEmpty>
        <ComboboxList>
          {(item: SkillOption) => (
            <ComboboxItem key={item.slug} value={item}>
              <div className="flex min-w-0 flex-1 items-center justify-between gap-2">
                <span className="truncate text-sm">{item.name}</span>
                <span className="text-muted-foreground truncate font-mono text-xs">
                  {item.isGlobal ? t("platform") : item.slug}
                </span>
              </div>
            </ComboboxItem>
          )}
        </ComboboxList>
      </ComboboxContent>
    </Combobox>
  );
}
