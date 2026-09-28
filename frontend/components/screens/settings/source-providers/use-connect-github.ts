"use client";

import { useMutation } from "@apollo/client/react";
import { toast } from "sonner";

import type { ConnectExistingGithubAppInput } from "@/graphql/__generated__/schema";
import { CONNECT_EXISTING_GITHUB_APP } from "@/graphql/scm/scm.mutations";
import { LIST_SOURCE_CONNECTIONS } from "@/graphql/scm/scm.queries";
import type { AstroliftSourceConnection, MutationResult } from "@/graphql/scm/scm.types";

interface ConnectExistingResp {
  connectExistingGithubApp: MutationResult<
    Pick<AstroliftSourceConnection, "id" | "name" | "accountLogin" | "installationId">
  >;
}

/**
 * "Connect to GitHub": launches the App-manifest create flow, or adopts an
 * existing App via connectExistingGithubApp. The data half of
 * ConnectGitHubDialogView; the view owns the form.
 */
export function useConnectGitHub() {
  const [connectExisting, { loading }] = useMutation<ConnectExistingResp>(
    CONNECT_EXISTING_GITHUB_APP,
    {
      refetchQueries: [{ query: LIST_SOURCE_CONNECTIONS }],
      awaitRefetchQueries: true,
    }
  );

  /** Hand off to auth1's manifest flow; `orgSlug` is already validated. */
  function startManifestFlow(orgSlug: string) {
    const params = new URLSearchParams();
    if (orgSlug) params.set("org", orgSlug);
    params.set("return_to", "/providers#source");
    window.location.href = `/app/auth1/scm/github/app-manifest/start?${params.toString()}`;
  }

  /** Resolves true when the App was verified and stored (close the sheet). */
  async function onConnectExisting(input: ConnectExistingGithubAppInput): Promise<boolean> {
    const { data } = await connectExisting({ variables: { input } });
    const res = data?.connectExistingGithubApp;
    if (res?.ok) {
      toast.success(`Connected GitHub App: ${res.data?.name ?? input.appId}`);
      return true;
    }
    toast.error(res?.errors?.[0]?.message ?? "Couldn't connect the GitHub App");
    return false;
  }

  return { loading, startManifestFlow, onConnectExisting };
}
