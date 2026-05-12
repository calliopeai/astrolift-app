"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import {
  AlertTriangleIcon,
  KeyRoundIcon,
  LogOutIcon,
  MonitorIcon,
  ShieldCheckIcon,
} from "lucide-react";
import Link from "next/link";
import * as React from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { LOGOUT_ALL_SESSIONS } from "@/graphql/identity/identity.mutations";
import { LIST_ACTIVE_SESSIONS } from "@/graphql/identity/identity.queries";
import type {
  AstroliftActiveSession,
  AstroliftLogoutAllSessionsPayload,
  MutationResult,
} from "@/graphql/identity/identity.types";

interface SessionsResp {
  astroliftActiveSessions: AstroliftActiveSession[];
}

export function SecuritySettingsClient() {
  const { data, loading, error, refetch } = useQuery<SessionsResp>(
    LIST_ACTIVE_SESSIONS,
    { fetchPolicy: "cache-and-network" },
  );

  const [logoutAll, { loading: signingOut }] = useMutation<{
    logoutAllSessions: MutationResult<AstroliftLogoutAllSessionsPayload>;
  }>(LOGOUT_ALL_SESSIONS);

  const sessions = data?.astroliftActiveSessions ?? [];
  const otherCount = sessions.filter((s) => !s.isCurrent).length;

  async function handleSignOutAll() {
    if (otherCount === 0) {
      toast.info("No other sessions to sign out.");
      return;
    }
    if (
      !confirm(
        `Sign out of ${otherCount} other session${otherCount === 1 ? "" : "s"}? Your current session stays signed in.`,
      )
    ) {
      return;
    }
    const { data: resp } = await logoutAll({
      variables: { input: { keepCurrent: true } },
    });
    if (resp?.logoutAllSessions.ok) {
      toast.success(
        `Signed out of ${resp.logoutAllSessions.data?.revokedCount ?? 0} session(s).`,
      );
      refetch();
    } else {
      toast.error(
        resp?.logoutAllSessions.errors?.[0]?.message ?? "Sign-out failed",
      );
    }
  }

  return (
    <div className="grid gap-4">
      <Card>
        <CardHeader className="flex flex-row items-start justify-between gap-3 space-y-0">
          <div>
            <CardTitle className="flex items-center gap-2 text-base">
              <MonitorIcon className="size-4" /> Active sessions
            </CardTitle>
            <CardDescription>
              Browser and mobile sessions currently signed in as you.
            </CardDescription>
          </div>
          <Button
            onClick={handleSignOutAll}
            variant="outline"
            disabled={signingOut || otherCount === 0}
          >
            <LogOutIcon className="size-4" />
            {signingOut ? "Signing out…" : "Sign out everywhere"}
          </Button>
        </CardHeader>
        <CardContent>
          {loading && !data ? (
            <div className="space-y-2">
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
            </div>
          ) : error ? (
            <div className="border-destructive/40 bg-destructive/5 flex items-start gap-2 rounded-md border p-3 text-sm">
              <AlertTriangleIcon className="text-destructive mt-0.5 size-4" />
              <div className="flex-1">
                <p className="text-destructive font-medium">
                  Couldn&apos;t load sessions
                </p>
                <p className="text-muted-foreground text-xs">{error.message}</p>
              </div>
            </div>
          ) : sessions.length === 0 ? (
            <p className="text-muted-foreground text-sm">
              No active sessions found.
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Session</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Expires</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {sessions.map((s) => (
                  <TableRow key={s.id}>
                    <TableCell className="font-mono text-xs">…{s.id}</TableCell>
                    <TableCell>
                      {s.isCurrent ? (
                        <Badge className="bg-emerald-500/15 text-emerald-700 dark:text-emerald-300">
                          this device
                        </Badge>
                      ) : (
                        <Badge variant="secondary">other device</Badge>
                      )}
                    </TableCell>
                    <TableCell className="text-muted-foreground text-xs">
                      {new Date(s.expiresAt).toLocaleString()}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <KeyRoundIcon className="size-4" /> API tokens
            </CardTitle>
            <CardDescription>
              Long-lived credentials for CLIs, CI runs, and scripts.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Button asChild variant="outline">
              <Link href="/tokens">Manage tokens</Link>
            </Button>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <ShieldCheckIcon className="size-4" /> Multi-factor authentication
            </CardTitle>
            <CardDescription>
              Configured at the IdP layer. Astrolift sees the assertion via OIDC.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <p className="text-muted-foreground text-sm">
              Set up MFA in your identity provider (Auth0 / Okta / Azure AD /
              Google Workspace). The platform enforces session-freshness for
              high-risk actions via ABAC policies.
            </p>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
