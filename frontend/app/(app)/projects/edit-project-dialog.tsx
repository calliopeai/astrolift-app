"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { AlertCircleIcon, CheckCircle2Icon, Loader2Icon, Wand2Icon } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

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
import { UPDATE_PROJECT } from "@/graphql/identity/identity.mutations";
import { LIST_PROJECTS, PROJECT_SLUG_AVAILABLE } from "@/graphql/identity/identity.queries";
import type { AstroliftProject, MutationResult } from "@/graphql/identity/identity.types";
import { useDebounce } from "@/hooks/use-debounce";
import { generateFriendlySlug } from "@/lib/friendly-name";

// Mirrors the backend project_slug naming rule (core/naming.py):
// lowercase, digits, dashes; must start with a letter and not end with a
// dash; ≤40 chars. Kept in lockstep with the server.
const SLUG_RE = /^[a-z]([a-z0-9-]*[a-z0-9])?$/;
const SLUG_MAX = 40;
const isValidSlug = (s: string) => SLUG_RE.test(s) && s.length <= SLUG_MAX;

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  project: AstroliftProject | null;
}

export function EditProjectDialog({ open, onOpenChange, project }: Props) {
  const [name, setName] = React.useState("");
  const [slug, setSlug] = React.useState("");

  // Seed the form from the project being edited by adjusting state
  // during render (guarded by the seeded id) rather than in an effect:
  // this avoids a cascading-render lint warning and, unlike a key
  // remount, preserves the sheet's close animation. Reset on close so
  // reopening the same project re-seeds from persisted values,
  // discarding unsaved edits. See react.dev "You Might Not Need an
  // Effect".
  const [seededId, setSeededId] = React.useState<string | null>(null);
  if (open && project && seededId !== project.id) {
    setSeededId(project.id);
    setName(project.name);
    setSlug(project.slug);
  } else if (!open && seededId !== null) {
    setSeededId(null);
  }

  const trimmedSlug = slug.trim();
  const debouncedSlug = useDebounce(trimmedSlug, 300);
  const unchangedSlug = project != null && trimmedSlug === project.slug;
  const formatValid = isValidSlug(trimmedSlug);

  // A project slug is unique per team, so the availability check is
  // scoped to the project's own team.
  const shouldCheck =
    open &&
    project != null &&
    formatValid &&
    !unchangedSlug &&
    isValidSlug(debouncedSlug) &&
    project.slug !== debouncedSlug;

  const avail = useQuery<{ astroliftProjectSlugAvailable: boolean }>(PROJECT_SLUG_AVAILABLE, {
    variables: { teamId: project?.team.id, slug: debouncedSlug, excludeId: project?.id },
    skip: !shouldCheck,
    fetchPolicy: "cache-and-network",
  });

  const [updateProject, { loading }] = useMutation<{
    updateProject: MutationResult<AstroliftProject>;
  }>(UPDATE_PROJECT, { refetchQueries: [{ query: LIST_PROJECTS }], awaitRefetchQueries: true });

  const debouncedSettled = debouncedSlug === trimmedSlug && !avail.loading;
  let slugStatus: "empty" | "invalid" | "unchanged" | "checking" | "available" | "taken";
  if (trimmedSlug === "") {
    slugStatus = "empty";
  } else if (!formatValid) {
    slugStatus = "invalid";
  } else if (unchangedSlug) {
    slugStatus = "unchanged";
  } else if (!shouldCheck || !debouncedSettled || avail.data == null) {
    slugStatus = "checking";
  } else {
    slugStatus = avail.data.astroliftProjectSlugAvailable ? "available" : "taken";
  }

  const nameValid = name.trim().length > 0;
  const slugOk = slugStatus === "available" || slugStatus === "unchanged";
  const dirty = project != null && (name.trim() !== project.name || trimmedSlug !== project.slug);
  const canSubmit = project != null && nameValid && slugOk && dirty && !loading;

  function handleGenerate() {
    setSlug(generateFriendlySlug());
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!project || !canSubmit) return;
    const { data } = await updateProject({
      variables: { input: { id: project.id, name: name.trim(), slug: trimmedSlug } },
    });
    const result = data?.updateProject;
    if (result?.ok) {
      toast.success(`Project saved as ${trimmedSlug}`);
      onOpenChange(false);
    } else {
      toast.error(result?.errors?.[0]?.message ?? "Save failed");
    }
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="flex flex-col">
        <SheetHeader>
          <SheetTitle>Edit project</SheetTitle>
          <SheetDescription>
            Rename this project or change its slug. Slugs are unique within
            the team{project ? ` (${project.team.slug})` : ""} and are used
            in URLs.
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
                onClick={handleGenerate}
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
              {loading ? "Saving…" : "Save changes"}
            </Button>
          </SheetFooter>
        </form>
      </SheetContent>
    </Sheet>
  );
}

/**
 * Inline valid / taken / checking indicator rendered under the slug
 * input. Kept local to this dialog (the team rename dialog carries its
 * own copy) so each feature form stays self-contained, matching the
 * create dialogs.
 */
function SlugStatusHint({
  status,
  kind,
}: {
  status: "empty" | "invalid" | "unchanged" | "checking" | "available" | "taken";
  kind: "team" | "project";
}) {
  const scope = kind === "team" ? "organization" : "team";
  switch (status) {
    case "invalid":
      return (
        <p className="text-destructive flex items-center gap-1.5 text-xs">
          <AlertCircleIcon className="size-3.5" />
          Lowercase letters, numbers, and hyphens; must start with a letter (max 40).
        </p>
      );
    case "checking":
      return (
        <p className="text-muted-foreground flex items-center gap-1.5 text-xs">
          <Loader2Icon className="size-3.5 animate-spin" />
          Checking availability…
        </p>
      );
    case "available":
      return (
        <p className="flex items-center gap-1.5 text-xs text-success-fg">
          <CheckCircle2Icon className="size-3.5" />
          Available
        </p>
      );
    case "taken":
      return (
        <p className="text-destructive flex items-center gap-1.5 text-xs">
          <AlertCircleIcon className="size-3.5" />
          Already taken in this {scope}.
        </p>
      );
    case "unchanged":
      return <p className="text-muted-foreground text-xs">Current slug — used in URLs.</p>;
    default:
      return <p className="text-muted-foreground text-xs">Lowercase letters, numbers, hyphens. Used in URLs.</p>;
  }
}
