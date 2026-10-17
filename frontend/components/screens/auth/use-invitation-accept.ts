"use client";

import { useMutation } from "@apollo/client/react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import * as React from "react";

import { ACCEPT_INVITATION } from "@/graphql/identity/identity.mutations";
import type { AstroliftInvitation, MutationResult } from "@/graphql/identity/identity.types";
import { invitationPath } from "@/lib/auth/sign-in-return";
import { clearToken } from "@/lib/auth/token-store";

import type { InvitationAcceptProps, InvitationSummary, InvitationView } from "./InvitationAccept";

interface AcceptResp {
  acceptInvitation: MutationResult<AstroliftInvitation>;
}

/** `POST /app/auth1/invitation-preview`: the invitation as a signed-out browser may see it. */
export interface InvitationPreview {
  status: "pending" | "accepted" | "expired" | "revoked" | "not_found";
  invitation?: InvitationSummary;
  viewer: { signedIn: false } | { signedIn: true; email: string; isInvitee: boolean };
}

const apiRoot = process.env.NEXT_PUBLIC_API_ROOT ?? "";

/** Reads the preview. Sends the browser's session cookie, never a token. */
export async function fetchInvitationPreview(token: string): Promise<InvitationPreview> {
  const res = await fetch(`${apiRoot}/app/auth1/invitation-preview`, {
    method: "POST",
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
      "X-Platform": "web",
    },
    body: JSON.stringify({ token }),
  });
  if (!res.ok) throw new Error(`invitation preview answered ${res.status}`);
  return (await res.json()) as InvitationPreview;
}

/** The step a preview puts the page on. */
export function invitationView(
  preview: InvitationPreview,
  accept: { accepting: boolean; errorMessage: string | null }
): InvitationView {
  const { invitation, viewer } = preview;
  if (preview.status === "not_found") return { kind: "notFound" };
  if (!invitation) return { kind: "unavailable" };
  switch (preview.status) {
    case "expired":
      return { kind: "expired", invitation };
    case "revoked":
      return { kind: "revoked", invitation };
    case "accepted":
      return viewer.signedIn && viewer.isInvitee
        ? { kind: "joined", invitation }
        : { kind: "used", invitation, signedIn: viewer.signedIn };
    case "pending":
      if (!viewer.signedIn) return { kind: "signIn", invitation };
      if (!viewer.isInvitee) return { kind: "wrongAccount", invitation, viewerEmail: viewer.email };
      return { kind: "accept", invitation, ...accept };
    default:
      return { kind: "unavailable" };
  }
}

/**
 * Takes up an org invitation by its single-use token. The page reads the
 * public preview first and only sends the authenticated accept mutation once
 * the preview says this browser is signed in as the invitee; signed out, the
 * one action is signing in, which comes back to this page.
 */
export function useInvitationAccept(token: string): InvitationAcceptProps {
  const router = useRouter();
  const t = useTranslations("invitationAccept");
  const [accept, { loading: accepting }] = useMutation<AcceptResp>(ACCEPT_INVITATION);
  const [preview, setPreview] = React.useState<InvitationPreview | null>(null);
  const [failed, setFailed] = React.useState(false);
  const [reads, setReads] = React.useState(0);
  const [joined, setJoined] = React.useState(false);
  const [errorMessage, setErrorMessage] = React.useState<string | null>(null);

  React.useEffect(() => {
    let current = true;
    fetchInvitationPreview(token).then(
      (next) => {
        if (!current) return;
        setPreview(next);
        setFailed(false);
      },
      () => {
        if (current) setFailed(true);
      }
    );
    return () => {
      current = false;
    };
  }, [token, reads]);

  async function onAccept() {
    setErrorMessage(null);
    try {
      const { data } = await accept({ variables: { input: { token } } });
      const result = data?.acceptInvitation;
      if (result?.ok) {
        setJoined(true);
        return;
      }
      setErrorMessage(result?.errors?.[0]?.message ?? t("acceptFailed"));
    } catch {
      setErrorMessage(t("acceptFailed"));
    }
    // The invitation may have expired, been revoked or used meanwhile, or
    // the session ended: the preview says which.
    setReads((n) => n + 1);
  }

  function onRetry() {
    setFailed(false);
    setPreview(null);
    setReads((n) => n + 1);
  }

  function onSignIn() {
    router.push(`/auth/login?next=${encodeURIComponent(invitationPath(token))}`);
  }

  function onSignOut() {
    clearToken();
    window.location.href = `${apiRoot}/app/auth1/logout`;
  }

  function onGoToDashboard() {
    router.push("/dashboard");
  }

  let view: InvitationView;
  if (failed && !preview) view = { kind: "unavailable" };
  else if (!preview) view = { kind: "loading" };
  else if (joined && preview.invitation) view = { kind: "joined", invitation: preview.invitation };
  else view = invitationView(preview, { accepting, errorMessage });

  return { view, onSignIn, onAccept, onSignOut, onRetry, onGoToDashboard };
}
