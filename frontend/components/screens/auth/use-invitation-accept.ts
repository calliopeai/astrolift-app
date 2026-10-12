"use client";

import { useMutation } from "@apollo/client/react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import * as React from "react";

import { ACCEPT_INVITATION } from "@/graphql/identity/identity.mutations";
import type { AstroliftInvitation, MutationResult } from "@/graphql/identity/identity.types";

interface AcceptResp {
  acceptInvitation: MutationResult<AstroliftInvitation>;
}

/** Accepts an org invitation by its single-use token. */
export function useInvitationAccept(token: string) {
  const router = useRouter();
  const t = useTranslations("invitationAccept");
  const [accept, { loading, data }] = useMutation<AcceptResp>(ACCEPT_INVITATION);
  const [submitted, setSubmitted] = React.useState(false);

  const result = data?.acceptInvitation;
  const ok = Boolean(submitted && result?.ok);
  const errorMessage =
    submitted && result && !result.ok ? (result.errors?.[0]?.message ?? t("failed")) : null;

  async function onAccept() {
    setSubmitted(true);
    try {
      await accept({ variables: { input: { token } } });
    } catch {
      // GraphQL errorLink already handles UNAUTHENTICATED → redirect
      // to login. Other errors surface via result.errors.
    }
  }

  function onGoToDashboard() {
    router.push("/dashboard");
  }

  return { loading, ok, errorMessage, onAccept, onGoToDashboard };
}
