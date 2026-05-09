"use client";

import { useMutation } from "@apollo/client/react";
import { CheckCircle2Icon, MailIcon, XCircleIcon } from "lucide-react";
import { useRouter } from "next/navigation";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ACCEPT_INVITATION } from "@/graphql/identity/identity.mutations";
import type {
  AstroliftInvitation,
  MutationResult,
} from "@/graphql/identity/identity.types";

interface AcceptResp {
  acceptInvitation: MutationResult<AstroliftInvitation>;
}

export function InvitationAcceptClient({ token }: { token: string }) {
  const router = useRouter();
  const [accept, { loading, data }] = useMutation<AcceptResp>(ACCEPT_INVITATION);
  const [submitted, setSubmitted] = React.useState(false);

  const result = data?.acceptInvitation;
  const ok = submitted && result?.ok;
  const errorMessage = submitted && result && !result.ok
    ? result.errors?.[0]?.message ?? "Could not accept invitation"
    : null;

  async function handleAccept() {
    setSubmitted(true);
    try {
      await accept({ variables: { input: { token } } });
    } catch {
      // GraphQL errorLink already handles UNAUTHENTICATED → redirect
      // to login. Other errors surface via result.errors.
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-muted/30 p-6">
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
              <div className="bg-green-100 border border-green-300 rounded-md p-3 text-sm dark:bg-green-950/40 dark:border-green-900/60">
                <div className="flex items-center gap-2 font-medium">
                  <CheckCircle2Icon className="size-4" /> You&apos;re in
                </div>
                <p className="text-muted-foreground mt-1">
                  Welcome aboard. Your account now has membership in this
                  organization.
                </p>
              </div>
              <Button onClick={() => router.push("/dashboard")} className="w-full">
                Go to dashboard
              </Button>
            </>
          ) : errorMessage ? (
            <>
              <div className="bg-red-100 border border-red-300 rounded-md p-3 text-sm dark:bg-red-950/40 dark:border-red-900/60">
                <div className="flex items-center gap-2 font-medium">
                  <XCircleIcon className="size-4" /> Could not accept
                </div>
                <p className="text-muted-foreground mt-1">{errorMessage}</p>
              </div>
              <Button
                variant="outline"
                onClick={() => router.push("/dashboard")}
                className="w-full"
              >
                Continue to Astrolift
              </Button>
            </>
          ) : (
            <>
              <p className="text-muted-foreground text-sm">
                You were invited to join an Astrolift organization. Accepting
                creates a member record on your account and applies the role
                the inviter selected.
              </p>
              <p className="text-muted-foreground text-xs">
                The invitation token is single-use. Acceptance requires your
                signed-in email to match the invited address — if it
                doesn&apos;t, you&apos;ll see an error and no record is
                created.
              </p>
              <Button
                onClick={handleAccept}
                disabled={loading}
                className="w-full"
              >
                Accept invitation
              </Button>
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
