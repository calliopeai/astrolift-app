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
import { UPDATE_TEAM } from "@/graphql/identity/identity.mutations";
import { LIST_TEAMS, TEAM_SLUG_AVAILABLE } from "@/graphql/identity/identity.queries";
import type { AstroliftTeam, MutationResult } from "@/graphql/identity/identity.types";
import { useDebounce } from "@/hooks/use-debounce";
import { generateFriendlySlug } from "@/lib/friendly-name";

// Mirrors the backend team_slug naming rule (core/naming.py): lowercase,
// digits, dashes; must start with a letter and not end with a dash; ≤40
// chars. Kept in lockstep with the server so the inline indicator and the
// mutation agree on what "valid" means.
const SLUG_RE = /^[a-z]([a-z0-9-]*[a-z0-9])?$/;
const SLUG_MAX = 40;
const isValidSlug = (s: string) => SLUG_RE.test(s) && s.length <= SLUG_MAX;

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  team: AstroliftTeam | null;
}

export function EditTeamDialog({ open, onOpenChange, team }: Props) {
  const [name, setName] = React.useState("");
  const [slug, setSlug] = React.useState("");

  // Seed the form from the team being edited by adjusting state during
  // render (guarded by the seeded id) rather than in an effect: this
  // avoids a cascading-render lint warning and, unlike a key remount,
  // preserves the sheet's close animation. Reset on close so reopening
  // the same team re-seeds from persisted values, discarding unsaved
  // edits. See react.dev "You Might Not Need an Effect".
  const [seededId, setSeededId] = React.useState<string | null>(null);
  if (open && team && seededId !== team.id) {
    setSeededId(team.id);
    setName(team.name);
    setSlug(team.slug);
  } else if (!open && seededId !== null) {
    setSeededId(null);
  }

  const trimmedSlug = slug.trim();
  const debouncedSlug = useDebounce(trimmedSlug, 300);
  const unchangedSlug = team != null && trimmedSlug === team.slug;
  const formatValid = isValidSlug(trimmedSlug);

  // Only hit the backend when the slug is a real, changed candidate.
  // An unchanged or malformed slug is decided client-side.
  const shouldCheck =
    open &&
    team != null &&
    formatValid &&
    !unchangedSlug &&
    isValidSlug(debouncedSlug) &&
    team.slug !== debouncedSlug;

  const avail = useQuery<{ astroliftTeamSlugAvailable: boolean }>(TEAM_SLUG_AVAILABLE, {
    variables: { slug: debouncedSlug, excludeId: team?.id },
    skip: !shouldCheck,
    fetchPolicy: "cache-and-network",
  });

  const [updateTeam, { loading }] = useMutation<{ updateTeam: MutationResult<AstroliftTeam> }>(
    UPDATE_TEAM,
    { refetchQueries: [{ query: LIST_TEAMS }], awaitRefetchQueries: true }
  );

  // Derive a single slug status the UI + submit gate both read from.
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
    slugStatus = avail.data.astroliftTeamSlugAvailable ? "available" : "taken";
  }

  const nameValid = name.trim().length > 0;
  const slugOk = slugStatus === "available" || slugStatus === "unchanged";
  const dirty = team != null && (name.trim() !== team.name || trimmedSlug !== team.slug);
  const canSubmit = team != null && nameValid && slugOk && dirty && !loading;

  function handleGenerate() {
    setSlug(generateFriendlySlug());
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!team || !canSubmit) return;
    const { data } = await updateTeam({
      variables: { input: { id: team.id, name: name.trim(), slug: trimmedSlug } },
    });
    const result = data?.updateTeam;
    if (result?.ok) {
      toast.success(`Team saved as ${trimmedSlug}`);
      onOpenChange(false);
    } else {
      toast.error(result?.errors?.[0]?.message ?? "Save failed");
    }
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="flex flex-col">
        <SheetHeader>
          <SheetTitle>Edit team</SheetTitle>
          <SheetDescription>
            Rename this team or change its slug. Slugs are unique within
            the organization and are used in URLs.
          </SheetDescription>
        </SheetHeader>
        <form onSubmit={submit} className="flex flex-1 flex-col gap-4 px-4 pb-4">
          <div className="space-y-2">
            <Label htmlFor="edit-team-name">Display name</Label>
            <Input
              id="edit-team-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Backend"
              autoFocus
              required
            />
          </div>

          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <Label htmlFor="edit-team-slug">Slug</Label>
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
              id="edit-team-slug"
              value={slug}
              onChange={(e) => setSlug(e.target.value)}
              placeholder="backend"
              pattern="[a-z0-9-]+"
              required
              aria-invalid={slugStatus === "invalid" || slugStatus === "taken"}
            />
            <SlugStatusHint status={slugStatus} kind="team" />
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
 * input so the operator sees availability as they type. Kept local to
 * this dialog (the project rename dialog carries its own copy) so each
 * feature form stays self-contained, matching the create dialogs.
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
