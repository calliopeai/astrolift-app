import {
  BanIcon,
  CircleCheckIcon,
  ClockIcon,
  CloudOffIcon,
  MailIcon,
  SearchXIcon,
  UserXIcon,
} from "lucide-react";
import { useTranslations } from "next-intl";
import type * as React from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { DefinitionList } from "@/components/ui/definition-list";
import { useFormatters } from "@/lib/i18n/formatters";

import { AuthSplash } from "./AuthSplash";

/** What an invitation says, as its signed-out preview tells it. */
export interface InvitationSummary {
  organizationName: string;
  inviterName: string;
  roleName: string | null;
  /** The invited address with most of its local part hidden. */
  emailHint: string;
  /** ISO timestamp. */
  expiresAt: string;
}

/** Where the invitee is in taking up the invitation. */
export type InvitationView =
  | { kind: "loading" }
  /** The preview could not be read (network, rate limit, server). */
  | { kind: "unavailable" }
  | { kind: "notFound" }
  | { kind: "expired"; invitation: InvitationSummary }
  | { kind: "revoked"; invitation: InvitationSummary }
  /** Accepted already, by an account other than this browser's. */
  | { kind: "used"; invitation: InvitationSummary; signedIn: boolean }
  /** Pending and this browser is signed out. */
  | { kind: "signIn"; invitation: InvitationSummary }
  /** Pending and signed in as the invitee. */
  | {
      kind: "accept";
      invitation: InvitationSummary;
      accepting: boolean;
      errorMessage: string | null;
    }
  /** Pending, but this browser is signed in as somebody else. */
  | { kind: "wrongAccount"; invitation: InvitationSummary; viewerEmail: string }
  /** This browser's account is the invitation's member now. */
  | { kind: "joined"; invitation: InvitationSummary };

export interface InvitationAcceptProps {
  view: InvitationView;
  onSignIn: () => void;
  onAccept: () => void;
  onSignOut: () => void;
  onRetry: () => void;
  onGoToDashboard: () => void;
}

/** An invitation link: what it is for, then the one thing to do about it. */
export function InvitationAccept({
  view,
  onSignIn,
  onAccept,
  onSignOut,
  onRetry,
  onGoToDashboard,
}: InvitationAcceptProps) {
  const t = useTranslations("invitationAccept");

  switch (view.kind) {
    case "loading":
      return <AuthSplash message={t("checking")} />;
    case "unavailable":
      return (
        <Frame
          icon={<CloudOffIcon className="text-danger size-5" />}
          title={t("unavailableTitle")}
          description={t("unavailableBody")}
        >
          <Button onClick={onRetry} className="w-full">
            {t("retry")}
          </Button>
        </Frame>
      );
    case "notFound":
      return (
        <Frame
          icon={<SearchXIcon className="text-muted-foreground size-5" />}
          title={t("notFoundTitle")}
          description={t("notFoundBody")}
        >
          <Button variant="outline" onClick={onGoToDashboard} className="w-full">
            {t("continue")}
          </Button>
        </Frame>
      );
    case "expired":
      return (
        <Frame
          icon={<ClockIcon className="text-warning size-5" />}
          title={t("expiredTitle")}
          description={t("expiredBody", {
            inviter: view.invitation.inviterName,
            organization: view.invitation.organizationName,
          })}
          invitation={view.invitation}
          lapsed
        />
      );
    case "revoked":
      return (
        <Frame
          icon={<BanIcon className="text-danger size-5" />}
          title={t("revokedTitle")}
          description={t("revokedBody", {
            inviter: view.invitation.inviterName,
            organization: view.invitation.organizationName,
          })}
          invitation={view.invitation}
        />
      );
    case "used":
      return (
        <Frame
          icon={<MailIcon className="text-muted-foreground size-5" />}
          title={t("usedTitle")}
          description={
            view.signedIn
              ? t("usedOtherBody", { inviter: view.invitation.inviterName })
              : t("usedSignedOutBody")
          }
          invitation={view.invitation}
        >
          {view.signedIn ? (
            <Button variant="outline" onClick={onGoToDashboard} className="w-full">
              {t("continue")}
            </Button>
          ) : (
            <Button onClick={onSignIn} className="w-full">
              {t("signIn")}
            </Button>
          )}
        </Frame>
      );
    case "signIn":
      return (
        <Frame
          icon={<MailIcon className="size-5" />}
          title={t("invitedTitle", { organization: view.invitation.organizationName })}
          description={t("invitedBody", {
            inviter: view.invitation.inviterName,
            organization: view.invitation.organizationName,
          })}
          invitation={view.invitation}
        >
          <p className="text-muted-foreground text-sm">{t("signInHint")}</p>
          <Button onClick={onSignIn} className="w-full">
            {t("signInToAccept")}
          </Button>
        </Frame>
      );
    case "accept":
      return (
        <Frame
          icon={<MailIcon className="size-5" />}
          title={t("invitedTitle", { organization: view.invitation.organizationName })}
          description={t("invitedBody", {
            inviter: view.invitation.inviterName,
            organization: view.invitation.organizationName,
          })}
          invitation={view.invitation}
        >
          {view.errorMessage && (
            <div
              role="alert"
              className="bg-danger-bg text-danger-fg border-danger-border rounded-md border p-3 text-sm break-words"
            >
              {view.errorMessage}
            </div>
          )}
          <Button onClick={onAccept} disabled={view.accepting} className="w-full">
            {view.accepting ? t("accepting") : t("accept")}
          </Button>
        </Frame>
      );
    case "wrongAccount":
      return (
        <Frame
          icon={<UserXIcon className="text-warning size-5" />}
          title={t("wrongAccountTitle")}
          description={t("wrongAccountBody", {
            invited: view.invitation.emailHint,
            viewer: view.viewerEmail,
          })}
          invitation={view.invitation}
        >
          <Button variant="outline" onClick={onSignOut} className="w-full">
            {t("signOut")}
          </Button>
        </Frame>
      );
    case "joined":
      return (
        <Frame
          icon={<CircleCheckIcon className="text-success size-5" />}
          title={t("joinedTitle")}
          description={t("joinedBody", { organization: view.invitation.organizationName })}
        >
          <Button onClick={onGoToDashboard} className="w-full">
            {t("goToDashboard")}
          </Button>
        </Frame>
      );
  }
}

function Frame({
  icon,
  title,
  description,
  invitation,
  lapsed = false,
  children,
}: {
  icon: React.ReactNode;
  title: string;
  description: string;
  invitation?: InvitationSummary;
  /** The invitation has expired: its date reads as when it did. */
  lapsed?: boolean;
  children?: React.ReactNode;
}) {
  return (
    <Card className="w-full max-w-md">
      <CardHeader>
        <CardTitle className="flex items-start gap-2">
          <span className="mt-0.5 shrink-0">{icon}</span>
          <span className="min-w-0 break-words">{title}</span>
        </CardTitle>
        <CardDescription className="break-words">{description}</CardDescription>
      </CardHeader>
      {(invitation || children) && (
        <CardContent className="space-y-4">
          {invitation && <InvitationDetails invitation={invitation} lapsed={lapsed} />}
          {children}
        </CardContent>
      )}
    </Card>
  );
}

function InvitationDetails({
  invitation,
  lapsed,
}: {
  invitation: InvitationSummary;
  lapsed: boolean;
}) {
  const t = useTranslations("invitationAccept");
  const { formatDateTime } = useFormatters();
  return (
    <DefinitionList
      items={[
        { term: t("organization"), description: invitation.organizationName },
        { term: t("invitedBy"), description: invitation.inviterName },
        ...(invitation.roleName ? [{ term: t("role"), description: invitation.roleName }] : []),
        { term: t("sentTo"), description: invitation.emailHint },
        {
          term: lapsed ? t("expired") : t("expires"),
          description: (
            <time dateTime={invitation.expiresAt}>{formatDateTime(invitation.expiresAt)}</time>
          ),
        },
      ]}
    />
  );
}
