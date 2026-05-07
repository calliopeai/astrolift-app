"use client";

import { useMutation } from "@apollo/client/react";
import * as React from "react";
import { toast } from "sonner";

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
import { CREATE_PROJECT } from "@/graphql/identity/identity.mutations";
import { LIST_PROJECTS } from "@/graphql/identity/identity.queries";
import type {
  AstroliftProject,
  AstroliftTeam,
  MutationResult,
} from "@/graphql/identity/identity.types";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  teams: AstroliftTeam[];
}

const slugify = (s: string) =>
  s
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 100);

export function CreateProjectDialog({ open, onOpenChange, teams }: Props) {
  const [name, setName] = React.useState("");
  const [slug, setSlug] = React.useState("");
  const [description, setDescription] = React.useState("");
  const [teamId, setTeamId] = React.useState("");
  const [slugTouched, setSlugTouched] = React.useState(false);

  React.useEffect(() => {
    if (teams.length > 0 && !teamId) setTeamId(teams[0].id);
  }, [teams, teamId]);

  React.useEffect(() => {
    if (!open) {
      setName("");
      setSlug("");
      setDescription("");
      setSlugTouched(false);
    }
  }, [open]);

  const [createProject, { loading }] =
    useMutation<{ createProject: MutationResult<AstroliftProject> }>(CREATE_PROJECT, {
      refetchQueries: [{ query: LIST_PROJECTS }],
      awaitRefetchQueries: true,
    });

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!teamId) return;
    const finalSlug = slug || slugify(name);
    const { data } = await createProject({
      variables: {
        input: {
          teamId,
          name: name.trim(),
          slug: finalSlug,
          description: description.trim() || null,
        },
      },
    });
    const result = data?.createProject;
    if (result?.ok) {
      toast.success(`Project ${finalSlug} created`);
      onOpenChange(false);
    } else {
      toast.error(result?.errors?.[0]?.message ?? "Create failed");
    }
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="flex flex-col">
        <SheetHeader>
          <SheetTitle>New project</SheetTitle>
          <SheetDescription>
            Projects group apps that share a deploy cadence or stack.
            Slugs are unique within a team and reclaimable after delete.
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
              pattern="[a-z0-9-]+"
              required
            />
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
            <Button type="submit" disabled={loading || !name || !teamId}>
              {loading ? "Creating…" : "Create project"}
            </Button>
          </SheetFooter>
        </form>
      </SheetContent>
    </Sheet>
  );
}
