"use client";

import { useTranslations } from "next-intl";
import * as React from "react";

import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

import type { AgentProjectFields, useAgentProjectStep } from "./use-agent-project-step";

export type AgentProjectStepViewProps<S extends AgentProjectFields> = ReturnType<
  typeof useAgentProjectStep
> & {
  state: S;
  setState: React.Dispatch<React.SetStateAction<S>>;
};

/**
 * Minimal project selector — the only field `registerAgentRepo` needs beyond
 * the repo handle (`projectId`). Cloned down from the register-app wizard's
 * AppDetailsStep with the name / slug / description fields dropped: agents
 * derive those from their discovered manifests, so the operator only chooses
 * where the agents' apps live.
 */
export function AgentProjectStepView<S extends AgentProjectFields>({
  state,
  setState,
  allTeams,
  allProjects,
}: AgentProjectStepViewProps<S>) {
  const t = useTranslations("agentWizard.project");
  // Team selection is informational — it filters the project list. The
  // mutation takes a `projectId`, not a `teamId`.
  const [teamOverride, setTeamOverride] = React.useState<string | null>(null);
  const pickedProject = allProjects.find((p) => p.id === state.projectId);
  const teamId = teamOverride !== null ? teamOverride : (pickedProject?.team.id ?? "");

  const filteredProjects = teamId ? allProjects.filter((p) => p.team.id === teamId) : allProjects;

  return (
    <div className="flex flex-col gap-5">
      <p className="text-muted-foreground text-sm">{t("intro")}</p>

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-2">
          <Label htmlFor="team">{t("teamFilter")}</Label>
          <Select
            value={teamId || "__all__"}
            onValueChange={(v) => {
              const next = v === "__all__" ? "" : v;
              setTeamOverride(next);
              // If the current project doesn't belong to the new team, clear
              // it so the operator picks an in-scope one.
              if (next) {
                const picked = allProjects.find((p) => p.id === state.projectId);
                if (picked && picked.team.id !== next) {
                  setState((s) => ({ ...s, projectId: "" }));
                }
              }
            }}
          >
            <SelectTrigger id="team">
              <SelectValue placeholder={t("allTeams")} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__all__">{t("allTeams")}</SelectItem>
              {allTeams.map((t) => (
                <SelectItem key={t.id} value={t.id}>
                  {t.slug} <span className="text-muted-foreground">— {t.name}</span>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <p className="text-muted-foreground text-xs">{t("teamHelp")}</p>
        </div>
        <div className="space-y-2">
          <Label htmlFor="project">
            {t("project")} <span className="text-destructive">*</span>
          </Label>
          <Select
            value={state.projectId}
            onValueChange={(v) => setState((s) => ({ ...s, projectId: v }))}
          >
            <SelectTrigger id="project">
              <SelectValue
                placeholder={filteredProjects.length === 0 ? t("noProjects") : t("selectProject")}
              />
            </SelectTrigger>
            <SelectContent>
              {filteredProjects.map((p) => (
                <SelectItem key={p.id} value={p.id}>
                  <span className="font-mono text-xs">
                    {p.team.slug}/{p.slug}
                  </span>{" "}
                  <span className="text-muted-foreground">— {p.name}</span>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>
    </div>
  );
}
