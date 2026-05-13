"use client";

import { useQuery } from "@apollo/client/react";
import * as React from "react";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { LIST_PROJECTS, LIST_TEAMS } from "@/graphql/identity/identity.queries";
import type { AstroliftProject, AstroliftTeam } from "@/graphql/identity/identity.types";

import type { WizardState } from "../wizard-client";

interface TeamsResp {
  astroliftTeams: AstroliftTeam[];
}

interface ProjectsResp {
  astroliftProjects: AstroliftProject[];
}

const SLUG_RE = /^[a-z0-9-]{1,40}$/;

function slugify(s: string): string {
  return s
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 40);
}

interface Props {
  state: WizardState;
  setState: React.Dispatch<React.SetStateAction<WizardState>>;
  setValid: (valid: boolean) => void;
}

export function AppDetailsStep({ state, setState, setValid }: Props) {
  const teams = useQuery<TeamsResp>(LIST_TEAMS, {
    fetchPolicy: "cache-and-network",
  });
  const projects = useQuery<ProjectsResp>(LIST_PROJECTS, {
    fetchPolicy: "cache-and-network",
  });

  const allProjects = React.useMemo(() => projects.data?.astroliftProjects ?? [], [projects.data]);
  const allTeams = teams.data?.astroliftTeams ?? [];

  // Local "team" selection is informational — it filters the project
  // list. The mutation takes a `projectId`, not a `teamId`. We default
  // it from the currently-picked project rather than tracking it as
  // independent state synced via an effect.
  const [teamOverride, setTeamOverride] = React.useState<string | null>(null);
  const pickedProject = allProjects.find((p) => p.id === state.projectId);
  const teamId = teamOverride !== null ? teamOverride : (pickedProject?.team.id ?? "");

  // Auto-pick first project once data lands.
  React.useEffect(() => {
    if (!state.projectId && allProjects.length > 0) {
      setState((s) => (s.projectId ? s : { ...s, projectId: allProjects[0].id }));
    }
  }, [allProjects, state.projectId, setState]);

  const filteredProjects = teamId ? allProjects.filter((p) => p.team.id === teamId) : allProjects;

  const slugValid = SLUG_RE.test(state.slug);
  const nameValid = state.name.trim().length > 0;
  const projectValid = state.projectId !== "";

  React.useEffect(() => {
    setValid(nameValid && slugValid && projectValid);
  }, [nameValid, slugValid, projectValid, setValid]);

  return (
    <div className="flex flex-col gap-5">
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-2">
          <Label htmlFor="name">
            Name <span className="text-destructive">*</span>
          </Label>
          <Input
            id="name"
            value={state.name}
            onChange={(e) => {
              const v = e.target.value;
              setState((s) => ({
                ...s,
                name: v,
                slug: s.slugTouched ? s.slug : slugify(v),
              }));
            }}
            placeholder="API Gateway"
            autoFocus
            required
          />
          <p className="text-muted-foreground text-xs">Human-readable name shown in the UI.</p>
        </div>

        <div className="space-y-2">
          <Label htmlFor="slug">
            Slug <span className="text-destructive">*</span>
          </Label>
          <Input
            id="slug"
            value={state.slug}
            onChange={(e) =>
              setState((s) => ({
                ...s,
                slug: e.target.value,
                slugTouched: true,
              }))
            }
            placeholder="api-gateway"
            className="font-mono text-xs"
            required
          />
          {state.slug && !slugValid ? (
            <p className="text-destructive text-xs">
              Lowercase letters, digits, and hyphens only (max 40 chars).
            </p>
          ) : (
            <p className="text-muted-foreground text-xs">
              URL-safe identifier. Auto-derived from the name — override to taste.
            </p>
          )}
        </div>
      </div>

      <div className="space-y-2">
        <Label htmlFor="description">Description</Label>
        <Textarea
          id="description"
          value={state.description}
          onChange={(e) => setState((s) => ({ ...s, description: e.target.value }))}
          placeholder="What does this app do?"
          rows={3}
        />
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-2">
          <Label htmlFor="team">Team (filter)</Label>
          <Select
            value={teamId || "__all__"}
            onValueChange={(v) => {
              const next = v === "__all__" ? "" : v;
              setTeamOverride(next);
              // If the current project doesn't belong to the new team,
              // clear it so the operator picks an in-scope one.
              if (next) {
                const picked = allProjects.find((p) => p.id === state.projectId);
                if (picked && picked.team.id !== next) {
                  setState((s) => ({ ...s, projectId: "" }));
                }
              }
            }}
          >
            <SelectTrigger id="team">
              <SelectValue placeholder="All teams" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__all__">All teams</SelectItem>
              {allTeams.map((t) => (
                <SelectItem key={t.id} value={t.id}>
                  {t.slug} <span className="text-muted-foreground">— {t.name}</span>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <p className="text-muted-foreground text-xs">
            Optional filter. Apps belong to a project (which belongs to a team).
          </p>
        </div>
        <div className="space-y-2">
          <Label htmlFor="project">
            Project <span className="text-destructive">*</span>
          </Label>
          <Select
            value={state.projectId}
            onValueChange={(v) => setState((s) => ({ ...s, projectId: v }))}
          >
            <SelectTrigger id="project">
              <SelectValue
                placeholder={
                  filteredProjects.length === 0 ? "No projects in this scope" : "Select a project"
                }
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
