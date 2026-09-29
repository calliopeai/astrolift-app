import { CheckCircle2Icon, MailIcon, XCircleIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

import type { useInvitationAccept } from "./use-invitation-accept";

export type InvitationAcceptProps = ReturnType<typeof useInvitationAccept>;

/** Accept an org invitation: the explanation, then the outcome. */
export function InvitationAccept({
  loading,
  ok,
  errorMessage,
  onAccept,
  onGoToDashboard,
}: InvitationAcceptProps) {
  return (
    <div className="bg-muted/30 flex min-h-screen items-center justify-center p-6">
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <MailIcon className="size-5" />
            Accept invitation
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {ok ? (
            <>
              <div className="bg-success/10 border-success-border rounded-md border p-3 text-sm">
                <div className="flex items-center gap-2 font-medium">
                  <CheckCircle2Icon className="size-4" /> You&apos;re in
                </div>
                <p className="text-muted-foreground mt-1">
                  Welcome aboard. Your account now has membership in this organization.
                </p>
              </div>
              <Button onClick={onGoToDashboard} className="w-full">
                Go to dashboard
              </Button>
            </>
          ) : errorMessage ? (
            <>
              <div className="bg-danger/10 border-danger-border rounded-md border p-3 text-sm">
                <div className="flex items-center gap-2 font-medium">
                  <XCircleIcon className="size-4" /> Could not accept
                </div>
                <p className="text-muted-foreground mt-1">{errorMessage}</p>
              </div>
              <Button variant="outline" onClick={onGoToDashboard} className="w-full">
                Continue to Astrolift
              </Button>
            </>
          ) : (
            <>
              <p className="text-muted-foreground text-sm">
                You were invited to join an Astrolift organization. Accepting creates a member
                record on your account and applies the role the inviter selected.
              </p>
              <p className="text-muted-foreground text-xs">
                The invitation token is single-use. Acceptance requires your signed-in email to
                match the invited address — if it doesn&apos;t, you&apos;ll see an error and no
                record is created.
              </p>
              <Button onClick={onAccept} disabled={loading} className="w-full">
                Accept invitation
              </Button>
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
