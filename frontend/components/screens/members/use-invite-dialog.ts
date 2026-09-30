"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import { CREATE_INVITATION, REVOKE_INVITATION } from "@/graphql/identity/identity.mutations";
import { LIST_ROLES_I_CAN_GRANT, SEARCHABLE_USERS } from "@/graphql/identity/identity.queries";
import type {
  AstroliftInvitation,
  AstroliftRole,
  AstroliftSearchableUser,
  MutationResult,
} from "@/graphql/identity/identity.types";
import { useDebounce } from "@/hooks/use-debounce";

export interface InvitationCreated {
  invitation: AstroliftInvitation;
  plaintextToken: string;
  acceptUrlPath: string;
}

const SEARCH_DEBOUNCE_MS = 300;
const MIN_SEARCH_LENGTH = 2;

/**
 * The data half of InviteSheet. The typed email drives the de-dupe
 * search, so the form fields live here with the queries they feed; the
 * view only renders them.
 */
export function useInviteDialog(open: boolean) {
  const t = useTranslations("lists.inviteDialog");
  const [email, setEmail] = React.useState("");
  const [roleSlug, setRoleSlug] = React.useState<string>("");
  const [expiresInDays, setExpiresInDays] = React.useState<string>("7");

  // After a successful create the dialog flips to a "share this link"
  // view. The plaintext token is the only chance — we render it once
  // with a Copy button and surface the accept URL the recipient
  // clicks.
  const [created, setCreated] = React.useState<InvitationCreated | null>(null);

  const debouncedEmail = useDebounce(email, SEARCH_DEBOUNCE_MS);
  const searchQuery = debouncedEmail.trim();
  const shouldSearch = searchQuery.length >= MIN_SEARCH_LENGTH && created === null;

  const search = useQuery<{ astroliftSearchableUsers: AstroliftSearchableUser[] }>(
    SEARCHABLE_USERS,
    {
      variables: { query: searchQuery },
      skip: !shouldSearch,
      fetchPolicy: "cache-and-network",
    }
  );

  // Roles list is fetched here (instead of being passed in) so the
  // dialog ships its own perm-gated list (#418). The members surface
  // still uses the broader astroliftRoles query for the role-binding
  // tab; the dialog deliberately uses the narrower one because the
  // server would reject a grant attempt for an out-of-scope role
  // anyway and surfacing it in the picker is misleading.
  const roles = useQuery<{ astroliftRolesICanGrant: AstroliftRole[] }>(LIST_ROLES_I_CAN_GRANT, {
    fetchPolicy: "cache-and-network",
    skip: !open,
  });

  const [createInvite, { loading }] = useMutation<{
    createInvitation: MutationResult<InvitationCreated>;
  }>(CREATE_INVITATION, {
    refetchQueries: ["ListInvitationsPage"],
    awaitRefetchQueries: true,
  });

  const [revokeInvite, { loading: revoking }] = useMutation<{
    revokeInvitation: MutationResult<AstroliftInvitation>;
  }>(REVOKE_INVITATION, {
    refetchQueries: [
      "ListInvitationsPage",
      { query: SEARCHABLE_USERS, variables: { query: searchQuery } },
    ],
    awaitRefetchQueries: true,
  });

  React.useEffect(() => {
    if (!open) {
      setEmail("");
      setRoleSlug("");
      setExpiresInDays("7");
      setCreated(null);
    }
  }, [open]);

  const grantableRoles = React.useMemo(() => {
    const list = roles.data?.astroliftRolesICanGrant ?? [];
    return list.filter((r) => r.scopeLevel === "ORG");
  }, [roles.data]);

  const matches = search.data?.astroliftSearchableUsers ?? [];
  const memberMatch = matches.find(
    (m) => m.matchKind === "MEMBER" && m.email.toLowerCase() === email.trim().toLowerCase()
  );
  const invitationMatch = matches.find(
    (m) => m.matchKind === "INVITATION" && m.email.toLowerCase() === email.trim().toLowerCase()
  );

  // A pending invitation for the exact same email blocks submission;
  // resending is a separate flow (revoke + re-invite). A member match
  // is informational only (the operator may still want to upgrade
  // their role via Grant role); we surface the badge but leave the
  // form enabled so the user explicitly cancels if needed.
  const blocksSubmit = invitationMatch !== undefined;

  async function onSubmit() {
    if (!email.trim()) {
      toast.error(t("toasts.emailRequired"));
      return;
    }
    if (blocksSubmit) {
      toast.error(t("toasts.invitationExists"));
      return;
    }
    const days = parseInt(expiresInDays, 10);
    const { data } = await createInvite({
      variables: {
        input: {
          email: email.trim(),
          roleSlug: roleSlug || null,
          expiresInDays: Number.isFinite(days) && days > 0 ? days : null,
        },
      },
    });
    const result = data?.createInvitation;
    if (result?.ok && result.data) {
      toast.success(t("toasts.created"));
      setCreated(result.data);
    } else {
      toast.error(result?.errors?.[0]?.message ?? t("toasts.failed"));
    }
  }

  async function onRevokeAndReinvite() {
    if (!invitationMatch?.invitationId) return;
    const { data } = await revokeInvite({
      variables: { input: { id: invitationMatch.invitationId } },
    });
    if (data?.revokeInvitation.ok) {
      toast.success(t("toasts.cancelledForReinvite"));
      // Refetch the search so the now-revoked invitation drops out
      // and the form unblocks.
      await search.refetch?.();
    } else {
      toast.error(data?.revokeInvitation.errors?.[0]?.message ?? t("toasts.cancelFailed"));
    }
  }

  function onCopyAcceptUrl() {
    if (!created) return;
    const url = buildAcceptUrlWithHint(created);
    navigator.clipboard
      .writeText(url)
      .then(() => toast.success(t("toasts.linkCopied")))
      .catch(() => toast.error(t("toasts.copyFailed")));
  }

  const noGrantableRoles =
    !roles.loading && (roles.data?.astroliftRolesICanGrant ?? []).length === 0;

  return {
    email,
    setEmail,
    roleSlug,
    setRoleSlug,
    expiresInDays,
    setExpiresInDays,
    created,
    acceptUrl: created && typeof window !== "undefined" ? buildAcceptUrlWithHint(created) : "",
    searchActive: shouldSearch,
    searchLoading: search.loading && !search.data,
    memberMatch,
    invitationMatch,
    blocksSubmit,
    grantableRoles,
    rolesLoading: roles.loading,
    noGrantableRoles,
    creating: loading,
    revoking,
    onSubmit,
    onRevokeAndReinvite,
    onCopyAcceptUrl,
  };
}

/**
 * Wraps the plaintext accept-url path with the absolute origin and
 * appends ``?exp=<unix>`` as a soft client-side hint (#418). The
 * server still authoritatively checks expiry on accept; the query
 * param is purely an affordance so a link-handler can present a
 * sensible "this link expired" message without hitting the API.
 */
function buildAcceptUrlWithHint(created: InvitationCreated): string {
  const origin = typeof window !== "undefined" ? window.location.origin : "";
  const expSec = Math.floor(new Date(created.invitation.expiresAt).getTime() / 1000);
  if (!Number.isFinite(expSec) || expSec <= 0) {
    return `${origin}${created.acceptUrlPath}`;
  }
  const join = created.acceptUrlPath.includes("?") ? "&" : "?";
  return `${origin}${created.acceptUrlPath}${join}exp=${expSec}`;
}
