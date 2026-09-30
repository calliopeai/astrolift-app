"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { useLocalListState } from "@/components/list/use-list-state";

import {
  LOGOUT_ALL_SESSIONS,
  REVOKE_ASTROLIFT_SESSION,
} from "@/graphql/identity/identity.mutations";
import { LIST_ACTIVE_SESSIONS } from "@/graphql/identity/identity.queries";
import type {
  AstroliftActiveSession,
  AstroliftLogoutAllSessionsPayload,
  AstroliftRevokeAstroliftSessionPayload,
  MutationResult,
} from "@/graphql/identity/identity.types";

import { SESSIONS_LIST } from "./sessions-list";

interface SessionsResp {
  astroliftActiveSessions: AstroliftActiveSession[];
}

/**
 * The account's active sessions and the two mutations that end them (sign
 * out everywhere else, revoke one). The data half of SecuritySettingsView.
 */
export function useSecuritySettings() {
  const t = useTranslations("securitySessions");
  // In memory: the Security settings page owns its query string.
  const list = useLocalListState(SESSIONS_LIST);
  const { data, loading, error, refetch } = useQuery<SessionsResp>(LIST_ACTIVE_SESSIONS, {
    fetchPolicy: "cache-and-network",
  });

  const [logoutAll, { loading: signingOut }] = useMutation<{
    logoutAllSessions: MutationResult<AstroliftLogoutAllSessionsPayload>;
  }>(LOGOUT_ALL_SESSIONS);

  const [revokeSession, { loading: revoking }] = useMutation<{
    revokeAstroliftSession: MutationResult<AstroliftRevokeAstroliftSessionPayload>;
  }>(REVOKE_ASTROLIFT_SESSION);

  const sessions = data?.astroliftActiveSessions ?? [];
  const otherCount = sessions.filter((s) => !s.isCurrent).length;

  /** Throws on failure so ConfirmDialog stays open and shows the error. */
  async function onSignOutAll(): Promise<void> {
    const { data: resp } = await logoutAll({
      variables: { input: { keepCurrent: true } },
    });
    if (resp?.logoutAllSessions.ok) {
      toast.success(
        t("toasts.signedOut", {
          count: resp.logoutAllSessions.data?.revokedCount ?? 0,
        })
      );
      refetch();
    } else {
      throw new Error(resp?.logoutAllSessions.errors?.[0]?.message ?? t("toasts.signOutFailed"));
    }
  }

  async function onRevoke(sessionId: string): Promise<boolean> {
    const { data: resp } = await revokeSession({
      variables: { input: { sessionId } },
    });
    if (resp?.revokeAstroliftSession.ok) {
      toast.success(
        resp.revokeAstroliftSession.data?.revoked ? t("toasts.revoked") : t("toasts.alreadyRevoked")
      );
      refetch();
      return true;
    }
    toast.error(resp?.revokeAstroliftSession.errors?.[0]?.message ?? t("toasts.revokeFailed"));
    return false;
  }

  return {
    list,
    sessions,
    otherCount,
    /** First load only: a background refetch keeps the table on screen. */
    loading: loading && !data,
    errorMessage: error ? error.message : null,
    signingOut,
    revoking,
    onSignOutAll,
    onRevoke,
  };
}
