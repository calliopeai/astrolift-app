"use client";

import * as React from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Textarea } from "@/components/ui/textarea";
import type { AstroliftTeam } from "@/graphql/identity/identity.types";

import { slugify, isValidSlug, SLUG_MAX, SLUG_INPUT_PATTERN } from "./project-team-slug";
import type { useCreateProject } from "./use-create-project";

export type CreateProjectSheetProps = ReturnType<typeof useCreateProject> & {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  teams: AstroliftTeam[];
  initialTeamSlug?: string | null;
};

export function CreateProjectSheet({
  open,
  onOpenChange,
  teams,
  initialTeamSlug,
  creating,
  createProject,
}: CreateProjectSheetProps) {
  const [name, setName] = React.useState("");
  const [slug, setSlug] = React.useState("");
  const [description, setDescription] = React.useState("");
  const [teamId, setTeamId] = React.useState("");
  const [slugTouched, setSlugTouched] = React.useState(false);

  React.useEffect(() => {
    if (open && teams.length > 0 && !teams.some((team) => team.id === teamId)) {
      const preferred = initialTeamSlug
        ? teams.find((team) => team.slug === initialTeamSlug)
        : teams[0];
      if (preferred) setTeamId(preferred.id);
    }
  }, [open, teams, teamId, initialTeamSlug]);

  React.useEffect(() => {
    if (!open) {
      setTeamId("");
      setName("");
      setSlug("");
      setDescription("");
      setSlugTouched(false);
    }
  }, [open]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (creating || !teamId || !name.trim() || !isValidSlug(slug)) return;
    if (await createProject({ teamId, name, slug, description })) onOpenChange(false);
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="flex flex-col">
        <SheetHeader>
          <SheetTitle>New project</SheetTitle>
          <SheetDescription>
            Projects group apps that share a deploy cadence or stack. Slugs are unique within a team
            and reclaimable after delete.
          </SheetDescription>
        </SheetHeader>
        <form onSubmit={submit} className="flex flex-1 flex-col gap-4 px-4 pb-4">
          <div className="space-y-2">
            <Label htmlFor="team">Team</Label>
            <Select value={teamId} onValueChange={setTeamId}>
              <SelectTrigger id="team">
                <SelectValue placeholder="Select a team" />
              </SelectTrigger>
              <SelectContent>
                {teams.map((t) => (
                  <SelectItem key={t.id} value={t.id}>
                    {t.organization.slug}/{t.slug}{" "}
                    <span className="text-muted-foreground">— {t.name}</span>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {teams.length === 0 && (
              <p className="text-muted-foreground text-xs">
                No teams yet. Create one on the Teams page first.
              </p>
            )}
          </div>

          <div className="space-y-2">
            <Label htmlFor="name">Display name</Label>
            <Input
              id="name"
              value={name}
              onChange={(e) => {
                setName(e.target.value);
                if (!slugTouched) setSlug(slugify(e.target.value));
              }}
              placeholder="API gateway"
              autoFocus
              required
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="slug">Slug</Label>
            <Input
              id="slug"
              value={slug}
              onChange={(e) => {
                setSlug(e.target.value);
                setSlugTouched(true);
              }}
              placeholder="api"
              pattern={SLUG_INPUT_PATTERN}
              maxLength={SLUG_MAX}
              aria-invalid={slug.length > 0 && !isValidSlug(slug)}
              required
            />
            <p className="text-muted-foreground text-xs">
              Start with a lowercase letter; use letters, numbers and hyphens, up to 40 characters.
              End with a letter or number.
            </p>
          </div>

          <div className="space-y-2">
            <Label htmlFor="description">Description (optional)</Label>
            <Textarea
              id="description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="What this project is for"
              rows={3}
            />
          </div>

          <SheetFooter className="mt-auto flex-row justify-end gap-2 px-0">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button
              type="submit"
              disabled={creating || !name.trim() || !teamId || !isValidSlug(slug)}
            >
              {creating ? "Creating…" : "Create project"}
            </Button>
          </SheetFooter>
        </form>
      </SheetContent>
    </Sheet>
  );
}
