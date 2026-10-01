"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import * as React from "react";
import { toast } from "sonner";
import { useTranslations } from "next-intl";
import { refetchAfterMutation } from "@/lib/apollo/mutation-feedback";

import { isValidSlug, type SlugStatus } from "@/components/screens/projects/project-team-slug";
import { UPDATE_TEAM } from "@/graphql/identity/identity.mutations";
import { LIST_TEAMS, TEAM_SLUG_AVAILABLE } from "@/graphql/identity/identity.queries";
import type { AstroliftTeam, MutationResult } from "@/graphql/identity/identity.types";
import { useDebounce } from "@/hooks/use-debounce";
import { generateFriendlySlug } from "@/lib/friendly-name";

/**
 * The data half of EditTeamSheet: the form values (the slug drives the
 * availability query), the derived slug status and submit gate, and the
 * update mutation.
 */
export function useEditTeam({ open, team }: { open: boolean; team: AstroliftTeam | null }) {
  const t = useTranslations("teams");
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
    {
      refetchQueries: [{ query: LIST_TEAMS }],
      onQueryUpdated: (query) => refetchAfterMutation(query, t("feedback.refreshWarning")),
      awaitRefetchQueries: true,
    }
  );

  // Derive a single slug status the UI + submit gate both read from.
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
    slugStatus = avail.data.astroliftTeamSlugAvailable ? "available" : "taken";
  }

  const nameValid = name.trim().length > 0;
  const slugOk = slugStatus === "available" || slugStatus === "unchanged";
  const dirty = team != null && (name.trim() !== team.name || trimmedSlug !== team.slug);
  const canSubmit = team != null && nameValid && slugOk && dirty && !loading;

  function generateSlug() {
    setSlug(generateFriendlySlug());
  }

  /** Resolves true when the team was saved, so the sheet can close. */
  async function save(): Promise<boolean> {
    if (!team || !canSubmit) return false;
    try {
      const { data } = await updateTeam({
        variables: { input: { id: team.id, name: name.trim(), slug: trimmedSlug } },
      });
      const result = data?.updateTeam;
      if (result?.ok) {
        toast.success(t("feedback.saved", { slug: trimmedSlug }));
        return true;
      }
      toast.error(result?.errors?.[0]?.message ?? t("feedback.saveFailed"));
    } catch (error) {
      toast.error(
        error instanceof Error && error.message ? error.message : t("feedback.saveFailed")
      );
    }
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
