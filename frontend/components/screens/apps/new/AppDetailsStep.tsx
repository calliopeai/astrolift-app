"use client";

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
import { cn } from "@/lib/utils";

import type { AppDetailsFields, useAppDetailsStep } from "./use-app-details-step";

function slugify(s: string): string {
  return s
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 40);
}

export type AppDetailsStepViewProps<S extends AppDetailsFields> = ReturnType<
  typeof useAppDetailsStep
> & {
  state: S;
  setState: React.Dispatch<React.SetStateAction<S>>;
};

/** Register-app step 3: name, slug, description, and the owning project. */
export function AppDetailsStepView<S extends AppDetailsFields>({
  state,
  setState,
  allTeams,
  allProjects,
  slugValid,
}: AppDetailsStepViewProps<S>) {
  // Local "team" selection is informational — it filters the project
  // list. The mutation takes a `projectId`, not a `teamId`. We default
  // it from the currently-picked project rather than tracking it as
  // independent state synced via an effect.
  const [teamOverride, setTeamOverride] = React.useState<string | null>(null);
  const pickedProject = allProjects.find((p) => p.id === state.projectId);
  const teamId = teamOverride !== null ? teamOverride : (pickedProject?.team.id ?? "");

  const filteredProjects = teamId ? allProjects.filter((p) => p.team.id === teamId) : allProjects;

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
            placeholder="lowercase, hyphens, 1-40 chars"
            className="font-mono text-xs"
            maxLength={40}
            required
          />
          <div className="flex items-start justify-between gap-2">
            {state.slug && !slugValid ? (
              <p className="text-destructive text-xs">
                Lowercase letters, digits, and hyphens only (max 40 chars).
              </p>
            ) : (
              <p className="text-muted-foreground text-xs">
                URL-safe identifier. Auto-derived from the name — override to taste.
              </p>
            )}
            <span
              className={cn(
                "text-2xs shrink-0 tabular-nums",
                state.slug.length >= 40
                  ? "text-destructive"
                  : state.slug.length >= 32
                    ? "text-warning-fg"
                    : "text-muted-foreground"
              )}
            >
              {state.slug.length}/40
            </span>
          </div>
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
