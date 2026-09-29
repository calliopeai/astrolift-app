"use client";

import { useLazyQuery, useMutation, useQuery } from "@apollo/client/react";
import * as React from "react";
import { toast } from "sonner";

import type { AstroliftAppAccess, AstroliftAppAccessPreview } from "@/graphql/__generated__/schema";
import {
  GET_APP_ACCESS,
  PREVIEW_APP_ACCESS,
  SET_APP_ACCESS,
} from "@/graphql/registry/registry.queries";

/** The app's access rule (#2132). The data half of AccessCardView. */
export function useAppAccess(appSlug: string) {
  const { data, loading } = useQuery<{ astroliftAppAccess: AstroliftAppAccess | null }>(
    GET_APP_ACCESS,
    { variables: { appSlug } }
  );
  return { access: data?.astroliftAppAccess ?? null, loading };
}

/**
 * The editable rule: the draft groups and users, the live "who could still
 * enter" preview they drive, and the save. The data half of AccessEditorView;
 * mounted only for an operator holding `app.access`.
 */
export function useAccessEditor(access: AstroliftAppAccess) {
  const [groups, setGroups] = React.useState<string[]>(access.groups);
  const [users, setUsers] = React.useState<string[]>(access.users);
  const [preview, previewState] = useLazyQuery<{
    astroliftAppAccessPreview: AstroliftAppAccessPreview | null;
  }>(PREVIEW_APP_ACCESS, { fetchPolicy: "network-only" });
  const [save, { loading: saving }] = useMutation<{
    setAppAccess: { ok: boolean; errors?: { message: string }[] | null };
  }>(SET_APP_ACCESS, {
    refetchQueries: [{ query: GET_APP_ACCESS, variables: { appSlug: access.appSlug } }],
  });

  const changed =
    groups.join("\n") !== access.groups.join("\n") || users.join("\n") !== access.users.join("\n");

  React.useEffect(() => {
    if (!changed) return;
    void preview({ variables: { appSlug: access.appSlug, groups, users } });
  }, [changed, groups, users, access.appSlug, preview]);

  async function onSave() {
    const { data } = await save({
      variables: { input: { appSlug: access.appSlug, groups, users } },
    });
    if (data?.setAppAccess.ok) {
      toast.success(groups.length || users.length ? "Access rule saved." : "Access rule removed.");
    } else {
      toast.error(data?.setAppAccess.errors?.[0]?.message ?? "Save failed.");
    }
  }

  return {
    groups,
    setGroups,
    users,
    setUsers,
    changed,
    preview: previewState.data?.astroliftAppAccessPreview ?? null,
    saving,
    onSave,
  };
}
