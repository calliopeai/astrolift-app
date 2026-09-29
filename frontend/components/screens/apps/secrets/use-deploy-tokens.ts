"use client";

import { useMutation } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import type { CursorPage } from "@/components/data-table";
import { useLocalListState } from "@/components/list/use-list-state";
import type { MutationResult } from "@/graphql/identity/identity.types";
import {
  CREATE_DEPLOY_TOKEN,
  REVOKE_DEPLOY_TOKEN,
  ROTATE_DEPLOY_TOKEN,
} from "@/graphql/lifecycle/lifecycle.mutations";
import { LIST_APP_DEPLOY_TOKENS_PAGE } from "@/graphql/lifecycle/lifecycle.queries";

import { useCursorList } from "../use-cursor-list";
import { APP_DEPLOY_TOKENS_LIST } from "./deploy-tokens-list";
import type { CreateDeployTokenInput, DeployToken, DeployTokenSecretReveal } from "./secrets.types";

interface Resp {
  astroliftAppDeployTokensPage: CursorPage<DeployToken>;
}

/**
 * The app's Access › Deploy tokens section: one cursor page of tokens for
 * the embedded list, the create / rotate / revoke mutations, and the
 * one-time plaintext reveal they produce. The data half of
 * DeployTokensScreen. The list state is in memory: the section lives under
 * `?section=tokens`, which a URL list state would drop on its first search.
 */
export function useDeployTokens(slug: string) {
  const tr = useTranslations("apps.tokens");
  const [reveal, setReveal] = React.useState<DeployTokenSecretReveal | null>(null);

  const tokens = useCursorList<DeployToken>({
    query: LIST_APP_DEPLOY_TOKENS_PAGE,
    variables: { appSlug: slug },
    extract: (d) => (d as Resp | undefined)?.astroliftAppDeployTokensPage,
    list: useLocalListState(APP_DEPLOY_TOKENS_LIST),
  });

  // Refetched by operation name, not by document: the cursor, page size and
  // search term live in the list state, so only the active query knows the
  // variables of the page the operator is looking at.
  const refetch = ["ListAppDeployTokensPage"];

  const [createToken, createState] = useMutation<{
    createDeployToken: MutationResult<DeployTokenSecretReveal>;
  }>(CREATE_DEPLOY_TOKEN, { refetchQueries: refetch, awaitRefetchQueries: true });
  const [rotateToken, rotateState] = useMutation<{
    rotateDeployToken: MutationResult<DeployTokenSecretReveal>;
  }>(ROTATE_DEPLOY_TOKEN, { refetchQueries: refetch, awaitRefetchQueries: true });
  const [revokeToken, revokeState] = useMutation<{
    revokeDeployToken: MutationResult<{ id: string; revoked: boolean }>;
  }>(REVOKE_DEPLOY_TOKEN, { refetchQueries: refetch, awaitRefetchQueries: true });

  const busy = createState.loading || rotateState.loading || revokeState.loading;

  /** Resolves true when the new secret is on screen (the view closes the sheet). */
  async function onCreate(input: CreateDeployTokenInput): Promise<boolean> {
    const { data } = await createToken({
      variables: { input: { appSlug: slug, ...input } },
    });
    if (data?.createDeployToken.ok) {
      const next = data.createDeployToken.data;
      if (next) {
        setReveal(next);
        return true;
      }
      return false;
    }
    toast.error(data?.createDeployToken.errors?.[0]?.message ?? tr("createFailed"));
    return false;
  }

  /** Throws on failure, so the confirm dialog stays open with the error. */
  async function onRotate(t: DeployToken) {
    const { data } = await rotateToken({ variables: { input: { id: t.id } } });
    if (data?.rotateDeployToken.ok) {
      const next = data.rotateDeployToken.data;
      if (next) setReveal(next);
    } else {
      throw new Error(data?.rotateDeployToken.errors?.[0]?.message ?? "Rotate failed");
    }
  }

  /** Throws on failure, so the confirm dialog stays open with the error. */
  async function onRevoke(token: DeployToken) {
    const { data } = await revokeToken({ variables: { input: { id: token.id } } });
    if (data?.revokeDeployToken.ok) {
      toast.success(tr("revokedToast", { name: token.name }));
    } else {
      throw new Error(data?.revokeDeployToken.errors?.[0]?.message ?? tr("revokeFailed"));
    }
  }

  return {
    slug,
    ...tokens,
    busy,
    reveal,
    onDismissReveal: () => setReveal(null),
    onCreate,
    onRotate,
    onRevoke,
  };
}
