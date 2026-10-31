"use client";

import { useLazyQuery, useMutation, useQuery } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import { gatedWrite } from "@/components/screens/brains/gated-write";
import type { AstroliftAppAccess, AstroliftAppAccessPreview } from "@/graphql/__generated__/schema";
import type {
  SetAppEdgePublicMutation,
  SetAppEdgePublicMutationVariables,
} from "@/graphql/__generated__/operations";
import {
  GET_APP_ACCESS,
  PREVIEW_APP_ACCESS,
  SET_APP_ACCESS,
  SET_APP_EDGE_PUBLIC,
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
  const t = useTranslations("apps.security.access");
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
      toast.success(groups.length || users.length ? t("saved") : t("removed"));
    } else {
      toast.error(data?.setAppAccess.errors?.[0]?.message ?? t("saveFailed"));
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

/**
 * The public switch (#2504): the app's platform URL answers without the
 * edge login. Making it public is a step-up action, so the write goes
 * through `gatedWrite`; making it private never asks. The data half of
 * PublicSwitchView; mounted only for an operator holding `app.access`.
 */
export function usePublicSwitch(access: AstroliftAppAccess) {
  const t = useTranslations("apps.security.public");
  const [confirming, setConfirming] = React.useState(false);
  const [refusal, setRefusal] = React.useState<string | null>(null);
  const [send, { loading: saving }] = useMutation<
    SetAppEdgePublicMutation,
    SetAppEdgePublicMutationVariables
  >(SET_APP_EDGE_PUBLIC, {
    refetchQueries: [{ query: GET_APP_ACCESS, variables: { appSlug: access.appSlug } }],
  });

  /** Resolves false on a refusal, which the view shows under the switch. */
  async function onSet(isPublic: boolean): Promise<boolean> {
    const outcome = await gatedWrite(
      async () =>
        (await send({ variables: { input: { appSlug: access.appSlug, public: isPublic } } })).data
          ?.setAppEdgePublic,
      t("saveFailed")
    );
    if (!outcome.ok) {
      setRefusal(outcome.message);
      return false;
    }
    setRefusal(null);
    toast.success(isPublic ? t("madePublic") : t("madePrivate"));
    return true;
  }

  return { confirming, setConfirming, refusal, saving, onSet };
}
