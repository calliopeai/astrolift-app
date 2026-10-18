"use client";

import { useTranslations } from "next-intl";
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
import { TemporaryAppFields } from "../TemporaryAppFields";
import type { TemporaryAppPolicyState } from "../temporary-app-model";
import { manifestExpiry } from "../temporary-app-model";

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

export type AppDetailsStepViewProps<S extends AppDetailsFields> = Omit<
  ReturnType<typeof useAppDetailsStep>,
  "temporary"
> & {
  temporary?: TemporaryAppPolicyState;
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
  projectsLoading = false,
  temporary,
}: AppDetailsStepViewProps<S>) {
  // Local "team" selection is informational — it filters the project
  // list. The mutation takes a `projectId`, not a `teamId`. We default
  // it from the currently-picked project rather than tracking it as
  // independent state synced via an effect.
  const [teamOverride, setTeamOverride] = React.useState<string | null>(null);
  const copy = useTranslations("appWizard.details");
  const pickedProject = allProjects.find((p) => p.id === state.projectId);
  const teamId = teamOverride !== null ? teamOverride : (pickedProject?.team.id ?? "");

  const filteredProjects = teamId ? allProjects.filter((p) => p.team.id === teamId) : allProjects;

  return (
    <div className="flex flex-col gap-5">
      <TemporaryAppFields
        value={state}
        onChange={(value) => setState((s) => ({ ...s, ...value }))}
        rules={temporary?.rules ?? null}
        loading={temporary?.loading}
        failed={temporary?.failed}
        onRetry={temporary?.onRetry}
        inherited={state.manifestLater ? "" : manifestExpiry(state.manifestRaw ?? "")}
      />
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-2">
          <Label htmlFor="name">
            {copy("name")} <span className="text-destructive">*</span>
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
            aria-invalid={!state.name.trim() || undefined}
            aria-describedby="name-help"
          />
          {state.name.trim() ? (
            <p id="name-help" className="text-muted-foreground text-xs">
              {copy("nameHelp")}
            </p>
          ) : (
            <p id="name-help" role="alert" className="text-destructive text-xs">
              {copy("nameRequired")}
            </p>
          )}
        </div>

        <div className="space-y-2">
          <Label htmlFor="slug">
            {copy("slug")} <span className="text-destructive">*</span>
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
            placeholder={copy("slugPlaceholder")}
            className="font-mono text-xs"
            maxLength={40}
            required
            aria-invalid={(state.slug !== "" && !slugValid) || !state.slug || undefined}
          />
          <div className="flex items-start justify-between gap-2">
            {!state.slug ? (
              <p role="alert" className="text-destructive text-xs">
                {copy("slugRequired")}
              </p>
            ) : !slugValid ? (
              <p role="alert" className="text-destructive text-xs">
                {copy("slugInvalid")}
              </p>
            ) : (
              <p className="text-muted-foreground text-xs">{copy("slugHelp")}</p>
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
        <Label htmlFor="description">{copy("description")}</Label>
        <Textarea
          id="description"
          value={state.description}
          onChange={(e) => setState((s) => ({ ...s, description: e.target.value }))}
          placeholder={copy("descriptionPlaceholder")}
          rows={3}
        />
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-2">
          <Label htmlFor="team">{copy("team")}</Label>
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
              <SelectValue placeholder={copy("allTeams")} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__all__">{copy("allTeams")}</SelectItem>
              {allTeams.map((t) => (
                <SelectItem key={t.id} value={t.id}>
                  {t.slug} <span className="text-muted-foreground">— {t.name}</span>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <p className="text-muted-foreground text-xs">{copy("teamHelp")}</p>
        </div>
        <div className="space-y-2">
          <Label htmlFor="project">
            {copy("project")} <span className="text-destructive">*</span>
          </Label>
          <Select
            value={state.projectId}
            onValueChange={(v) => setState((s) => ({ ...s, projectId: v }))}
          >
            <SelectTrigger id="project">
              <SelectValue
                placeholder={
                  filteredProjects.length === 0 ? copy("noProjects") : copy("selectProject")
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
          {!state.projectId && !projectsLoading && (
            <p role="alert" className="text-destructive text-xs">
              {filteredProjects.length === 0 ? copy("noProjectsHelp") : copy("projectRequired")}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
