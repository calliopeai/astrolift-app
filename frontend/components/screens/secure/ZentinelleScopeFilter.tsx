"use client";

import { useTranslations } from "next-intl";
import * as React from "react";

import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

import {
  WINDOWS,
  type ZentinelleScope,
  type ZentinelleScopeOptions,
  type ZentinelleWindow,
} from "./zentinelle-model";

export interface ZentinelleScopeFilterProps {
  scope: ZentinelleScope;
  onScopeChange: (scope: ZentinelleScope) => void;
  options: ZentinelleScopeOptions;
  /** Policies take an agent or a project, not a team. */
  teams?: boolean;
  range?: ZentinelleWindow;
  onRangeChange?: (range: ZentinelleWindow) => void;
}

function scopeValue(scope: ZentinelleScope): string {
  if (scope.kind === "project") return `project:${scope.id}`;
  if (scope.kind === "team") return `team:${scope.id}`;
  return scope.kind;
}

/**
 * Narrow a Zentinelle read to one agent, project or team, and a time window.
 * The server narrows again to what the viewer may see.
 */
export function ZentinelleScopeFilter({
  scope,
  onScopeChange,
  options,
  teams = true,
  range,
  onRangeChange,
}: ZentinelleScopeFilterProps) {
  const t = useTranslations("zentinelleScreen.filters");
  const [agentKind, setAgentKind] = React.useState(scope.kind === "agent");

  function pick(value: string) {
    setAgentKind(value === "agent");
    if (value === "agent") return;
    if (value.startsWith("project:")) onScopeChange({ kind: "project", id: value.slice(8) });
    else if (value.startsWith("team:")) onScopeChange({ kind: "team", id: value.slice(5) });
    else onScopeChange({ kind: "all" });
  }

  function commitAgent(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const slug = String(new FormData(event.currentTarget).get("agent") ?? "").trim();
    onScopeChange(slug ? { kind: "agent", slug } : { kind: "all" });
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      {range && onRangeChange && (
        <Select value={range} onValueChange={(v) => onRangeChange(v as ZentinelleWindow)}>
          <SelectTrigger size="sm" aria-label={t("window")} className="min-w-32 text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {WINDOWS.map((w) => (
              <SelectItem key={w} value={w}>
                {t(`windows.${w}`)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      )}
      <Select value={agentKind ? "agent" : scopeValue(scope)} onValueChange={pick}>
        <SelectTrigger size="sm" aria-label={t("scope")} className="min-w-40 text-xs">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">{t("all")}</SelectItem>
          <SelectItem value="agent">{t("agent")}</SelectItem>
          {options.projects.length > 0 && (
            <SelectGroup>
              <SelectLabel>{t("projects")}</SelectLabel>
              {options.projects.map((p) => (
                <SelectItem key={p.id} value={`project:${p.id}`}>
                  {p.name}
                </SelectItem>
              ))}
            </SelectGroup>
          )}
          {teams && options.teams.length > 0 && (
            <SelectGroup>
              <SelectLabel>{t("teams")}</SelectLabel>
              {options.teams.map((team) => (
                <SelectItem key={team.id} value={`team:${team.id}`}>
                  {team.name}
                </SelectItem>
              ))}
            </SelectGroup>
          )}
        </SelectContent>
      </Select>
      {agentKind && (
        <form onSubmit={commitAgent}>
          <Input
            name="agent"
            aria-label={t("agentSlug")}
            placeholder={t("agentSlug")}
            defaultValue={scope.kind === "agent" ? scope.slug : ""}
            onBlur={(e) => e.currentTarget.form?.requestSubmit()}
            className="h-8 w-48 text-xs"
          />
        </form>
      )}
    </div>
  );
}
