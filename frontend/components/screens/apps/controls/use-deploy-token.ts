"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { useState } from "react";
import { toast } from "sonner";

import {
  CREATE_DEPLOY_TOKEN,
  REVOKE_DEPLOY_TOKEN,
  ROTATE_DEPLOY_TOKEN,
} from "@/graphql/lifecycle/lifecycle.mutations";
import { LIST_APP_DEPLOY_TOKENS } from "@/graphql/lifecycle/lifecycle.queries";
import { GET_APP } from "@/graphql/registry/registry.queries";
import type { MutationResult } from "@/graphql/identity/identity.types";

export interface DeployToken {
  id: string;
  name: string;
  last4: string;
  scopes: string[];
  isRevoked: boolean;
  lastUsedAt: string | null;
  lastRotatedAt: string | null;
  createdAt: string;
}

interface DeployTokenSecretReveal {
  token: DeployToken;
  plaintextSecret: string;
}

interface ListResp {
  astroliftAppDeployTokens: DeployToken[];
}

interface CreateResp {
  createDeployToken: MutationResult<DeployTokenSecretReveal>;
}

interface RotateResp {
  rotateDeployToken: MutationResult<DeployTokenSecretReveal>;
}

interface RevokeResp {
  revokeDeployToken: MutationResult<{ id: string; revoked: boolean }>;
}

/**
 * The app's deploy token — generate, rotate, revoke. The plaintext is held
 * here exactly once, until dismissed; subsequent visits only see the
 * last-4 digits. The data half of DeployTokenControlView.
 */
export function useDeployToken(appSlug: string) {
  const { data, loading, refetch } = useQuery<ListResp>(LIST_APP_DEPLOY_TOKENS, {
    variables: { appSlug },
    fetchPolicy: "cache-and-network",
  });
  const tokens = data?.astroliftAppDeployTokens ?? [];
  const active = tokens.find((t) => !t.isRevoked) ?? null;
  const [reveal, setReveal] = useState<string | null>(null);

  const refetchAll = async () => {
    await refetch();
  };

  const [create, { loading: creating }] = useMutation<CreateResp>(CREATE_DEPLOY_TOKEN, {
    onCompleted: refetchAll,
  });
  const [rotate, { loading: rotating }] = useMutation<RotateResp>(ROTATE_DEPLOY_TOKEN, {
    onCompleted: refetchAll,
  });
  const [revoke, { loading: revoking }] = useMutation<RevokeResp>(REVOKE_DEPLOY_TOKEN, {
    refetchQueries: [
      { query: LIST_APP_DEPLOY_TOKENS, variables: { appSlug } },
      { query: GET_APP, variables: { slug: appSlug } },
    ],
    awaitRefetchQueries: true,
  });

  async function onCreate() {
    const { data } = await create({
      variables: {
        input: { appSlug, name: "default", scopes: ["deploy"] },
      },
    });
    if (data?.createDeployToken.ok && data.createDeployToken.data) {
      setReveal(data.createDeployToken.data.plaintextSecret);
      toast.success("Deploy token minted.");
    } else {
      toast.error(data?.createDeployToken.errors?.[0]?.message ?? "Create failed.");
    }
  }

  // Rotate and revoke throw on failure so ConfirmDialog holds open and
  // reports the reason.
  async function onRotate() {
    if (!active) return;
    const { data } = await rotate({
      variables: { input: { id: active.id } },
    });
    if (data?.rotateDeployToken.ok && data.rotateDeployToken.data) {
      setReveal(data.rotateDeployToken.data.plaintextSecret);
      toast.success("Token rotated — copy the new value now.");
    } else {
      throw new Error(data?.rotateDeployToken.errors?.[0]?.message ?? "Rotate failed.");
    }
  }

  async function onRevoke() {
    if (!active) return;
    const { data } = await revoke({
      variables: { input: { id: active.id } },
    });
    if (data?.revokeDeployToken.ok) {
      toast.success("Token revoked.");
    } else {
      throw new Error(data?.revokeDeployToken.errors?.[0]?.message ?? "Revoke failed.");
    }
  }

  return {
    loading,
    active,
    reveal,
    onDismissReveal: () => setReveal(null),
    creating,
    rotating,
    revoking,
    onCreate,
    onRotate,
    onRevoke,
  };
}
