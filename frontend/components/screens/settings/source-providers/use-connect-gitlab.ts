"use client";

import { useMutation } from "@apollo/client/react";
import { toast } from "sonner";

import type { ConnectSourceInput } from "@/graphql/__generated__/schema";
import { CONNECT_SOURCE } from "@/graphql/scm/scm.mutations";
import { LIST_SOURCE_CONNECTIONS } from "@/graphql/scm/scm.queries";
import type { AstroliftSourceConnection, MutationResult } from "@/graphql/scm/scm.types";

interface MutationResp {
  connectSource: MutationResult<AstroliftSourceConnection>;
}

/**
 * "Connect to GitLab": the callback URL Astrolift expects, the clipboard
 * copy of it, and the gitlab_oauth_app connectSource mutation. The data
 * half of ConnectGitLabDialogView; the view owns the form.
 */
export function useConnectGitLab() {
  const callbackUrl =
    typeof window !== "undefined"
      ? `${window.location.origin}/app/auth1/scm/gitlab/callback`
      : "/app/auth1/scm/gitlab/callback";

  const [connect, { loading }] = useMutation<MutationResp>(CONNECT_SOURCE, {
    refetchQueries: [{ query: LIST_SOURCE_CONNECTIONS }],
    awaitRefetchQueries: true,
  });

  /** Resolves true when the callback URL reached the clipboard. */
  async function copyCallback(): Promise<boolean> {
    try {
      await navigator.clipboard.writeText(callbackUrl);
      toast.success("Callback URL copied");
      return true;
    } catch {
      toast.error("Couldn't copy — select the text and copy manually");
      return false;
    }
  }

  /** Resolves true when the OAuth app row was stored (close the sheet). */
  async function onConnect(input: Omit<ConnectSourceInput, "appClientId">): Promise<boolean> {
    const { data } = await connect({ variables: { input } });
    if (data?.connectSource.ok) {
      toast.success(
        `Connected ${data.connectSource.data?.name ?? "GitLab"} — use "Connect my GitLab" to sign in.`
      );
      return true;
    }
    toast.error(data?.connectSource.errors?.[0]?.message ?? "Connect failed");
    return false;
  }

  return { loading, callbackUrl, copyCallback, onConnect };
}
