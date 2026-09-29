"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import * as React from "react";
import { toast } from "sonner";

import { UPDATE_PROJECT } from "@/graphql/identity/identity.mutations";
import { LIST_PROJECTS, PROJECT_SLUG_AVAILABLE } from "@/graphql/identity/identity.queries";
import type { AstroliftProject, MutationResult } from "@/graphql/identity/identity.types";
import { useDebounce } from "@/hooks/use-debounce";
import { generateFriendlySlug } from "@/lib/friendly-name";

import { isValidSlug, type SlugStatus } from "./project-team-slug";

/**
 * The data half of EditProjectSheet: the form values (the slug drives the
 * availability query), the derived slug status and submit gate, and the
 * update mutation.
 */
export function useEditProject({
  open,
  project,
}: {
  open: boolean;
  project: AstroliftProject | null;
}) {
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
  let slugStatus: SlugStatus;
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

  function generateSlug() {
    setSlug(generateFriendlySlug());
  }

  /** Resolves true when the project was saved, so the sheet can close. */
  async function save(): Promise<boolean> {
    if (!project || !canSubmit) return false;
    const { data } = await updateProject({
      variables: { input: { id: project.id, name: name.trim(), slug: trimmedSlug } },
    });
    const result = data?.updateProject;
    if (result?.ok) {
      toast.success(`Project saved as ${trimmedSlug}`);
      return true;
    }
    toast.error(result?.errors?.[0]?.message ?? "Save failed");
    return false;
  }

  return {
    name,
    setName,
    slug,
    setSlug,
    slugStatus,
    canSubmit,
    saving: loading,
    generateSlug,
    save,
  };
}
