"use client";

import { Wand2Icon } from "lucide-react";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import type { AstroliftProject } from "@/graphql/identity/identity.types";

import { SlugStatusHint } from "./SlugStatusHint";
import type { useEditProject } from "./use-edit-project";

export type EditProjectSheetProps = ReturnType<typeof useEditProject> & {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  project: AstroliftProject | null;
};

export function EditProjectSheet({
  open,
  onOpenChange,
  project,
  name,
  setName,
  slug,
  setSlug,
  slugStatus,
  canSubmit,
  saving,
  generateSlug,
  save,
}: EditProjectSheetProps) {
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (await save()) onOpenChange(false);
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="flex flex-col">
        <SheetHeader>
          <SheetTitle>Edit project</SheetTitle>
          <SheetDescription>
            Rename this project or change its slug. Slugs are unique within the team
            {project ? ` (${project.team.slug})` : ""} and are used in URLs.
          </SheetDescription>
        </SheetHeader>
        <form onSubmit={submit} className="flex flex-1 flex-col gap-4 px-4 pb-4">
          <div className="space-y-2">
            <Label htmlFor="edit-project-name">Display name</Label>
            <Input
              id="edit-project-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="API gateway"
              autoFocus
              required
            />
          </div>

          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <Label htmlFor="edit-project-slug">Slug</Label>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                className="h-6 gap-1 px-2 text-xs"
                onClick={generateSlug}
              >
                <Wand2Icon className="size-3" />
                Generate
              </Button>
            </div>
            <Input
              id="edit-project-slug"
              value={slug}
              onChange={(e) => setSlug(e.target.value)}
              placeholder="api"
              pattern="[a-z0-9-]+"
              required
              aria-invalid={slugStatus === "invalid" || slugStatus === "taken"}
            />
            <SlugStatusHint status={slugStatus} kind="project" />
          </div>

          <SheetFooter className="mt-auto flex-row justify-end gap-2 px-0">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={!canSubmit}>
              {saving ? "Saving…" : "Save changes"}
            </Button>
          </SheetFooter>
        </form>
      </SheetContent>
    </Sheet>
  );
}
